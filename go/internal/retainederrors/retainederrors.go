// Package retainederrors proves that a checked error escapes as typed result data.
package retainederrors

import (
	"go/ast"
	"go/constant"
	"go/token"
	"go/types"
	"reflect"
	"strconv"
	"strings"

	"golang.org/x/tools/go/analysis"
	"golang.org/x/tools/go/analysis/passes/buildssa"
	"golang.org/x/tools/go/ssa"

	"github.com/nikitatsym/tackbox/go/internal/astutil"
)

var Analyzer = &analysis.Analyzer{
	Name:       "retainederrors",
	Doc:        "prove checked error retention through typed aggregates and returned data",
	Requires:   []*analysis.Analyzer{buildssa.Analyzer},
	ResultType: reflect.TypeOf(Result{}),
	Run:        run,
}

// Result identifies error branches whose every returning path retains the error.
type Result map[token.Pos]bool

type paths map[string]bool

type state struct {
	values   map[ssa.Value]paths
	memory   map[ssa.Value]paths
	captured bool
	scalars  map[ssa.Value]bool
}

type constructor struct {
	function  *ssa.Function
	parameter int
}

type scan struct {
	summaries map[constructor][]paths
	active    map[constructor]bool
}

func run(pass *analysis.Pass) (any, error) {
	guards := map[token.Pos]token.Pos{}
	for _, file := range pass.Files {
		ast.Inspect(file, func(node ast.Node) bool {
			branch, _, ok := astutil.ErrBranch(pass.TypesInfo, node)
			if ok {
				guards[branch.Cond.(*ast.BinaryExpr).OpPos] = branch.Pos()
			}
			return true
		})
	}
	result := Result{}
	scanner := &scan{summaries: map[constructor][]paths{}, active: map[constructor]bool{}}
	for _, function := range pass.ResultOf[buildssa.Analyzer].(*buildssa.SSA).SrcFuncs {
		for _, block := range function.Blocks {
			for _, instruction := range block.Instrs {
				branch, ok := instruction.(*ssa.If)
				if !ok {
					continue
				}
				condition, ok := branch.Cond.(*ssa.BinOp)
				if !ok || guards[condition.Pos()] == token.NoPos {
					continue
				}
				errorValue := condition.X
				if isNil(errorValue) {
					errorValue = condition.Y
				}
				if !isError(errorValue.Type()) {
					continue
				}
				start := block.Succs[0]
				if condition.Op == token.EQL {
					start = block.Succs[1]
				}
				initial := newState()
				initial.values[errorValue] = paths{"": true}
				initial.scalars[errorValue] = true
				_, safe := scanner.evaluate(function, start, initial)
				result[guards[condition.Pos()]] = safe
			}
		}
	}
	return result, nil
}

func newState() state {
	return state{values: map[ssa.Value]paths{}, memory: map[ssa.Value]paths{}, scalars: map[ssa.Value]bool{}}
}

func copyPaths(value paths) paths {
	result := paths{}
	for path := range value {
		result[path] = true
	}
	return result
}

func (value state) clone() state {
	result := newState()
	result.captured = value.captured
	result.scalars = value.scalars
	for key, flow := range value.values {
		result.values[key] = copyPaths(flow)
	}
	for key, flow := range value.memory {
		result.memory[key] = copyPaths(flow)
	}
	return result
}

func intersect(destination, source paths) bool {
	changed := false
	for path := range destination {
		if !source[path] {
			delete(destination, path)
			changed = true
		}
	}
	return changed
}

func merge(destination *state, source state) bool {
	changed := false
	for _, pair := range [][2]map[ssa.Value]paths{{destination.values, source.values}, {destination.memory, source.memory}} {
		for value := range pair[1] {
			if _, exists := pair[0][value]; !exists {
				pair[0][value] = paths{}
			}
		}
		for value, flow := range pair[0] {
			if intersect(flow, pair[1][value]) {
				changed = true
			}
		}
	}
	if destination.captured && !source.captured {
		destination.captured = false
		changed = true
	}
	return changed
}

func address(value ssa.Value) (ssa.Value, string) {
	switch value := value.(type) {
	case *ssa.FieldAddr:
		root, prefix := address(value.X)
		return root, prefix + ".f" + strconv.Itoa(value.Field)
	case *ssa.IndexAddr:
		root, prefix := address(value.X)
		return root, prefix + ".e"
	case *ssa.Slice:
		return address(value.X)
	case *ssa.ChangeType:
		return address(value.X)
	case *ssa.Convert:
		return address(value.X)
	}
	return value, ""
}

func subset(flow paths, prefix string) paths {
	result := paths{}
	for path := range flow {
		if path == prefix || strings.HasPrefix(path, prefix+".") {
			result[strings.TrimPrefix(path, prefix)] = true
		}
	}
	return result
}

func (value state) get(expression ssa.Value) paths {
	if expression == nil {
		return paths{}
	}
	root, prefix := address(expression)
	result := subset(value.memory[root], prefix)
	for path := range value.values[expression] {
		result[path] = true
	}
	return result
}

func (value state) put(expression ssa.Value, flow paths) {
	if _, pointer := expression.Type().Underlying().(*types.Pointer); pointer && !value.scalars[expression] {
		root, prefix := address(expression)
		value.store(root, prefix, flow)
		return
	}
	value.values[expression] = copyPaths(flow)
}

func (value state) store(root ssa.Value, prefix string, flow paths) {
	if value.memory[root] == nil {
		value.memory[root] = paths{}
	}
	for path := range value.memory[root] {
		if path == prefix || strings.HasPrefix(path, prefix+".") {
			delete(value.memory[root], path)
		}
	}
	for path := range flow {
		value.memory[root][prefix+path] = true
	}
}

func retainedPaths(flow paths, value types.Type) paths {
	result := paths{}
	for path := range flow {
		current := value
		for _, segment := range strings.Split(strings.TrimPrefix(path, "."), ".") {
			if segment == "" {
				break
			}
			for {
				pointer, ok := current.Underlying().(*types.Pointer)
				if !ok {
					break
				}
				current = pointer.Elem()
			}
			switch kind := current.Underlying().(type) {
			case *types.Struct:
				index, err := strconv.Atoi(strings.TrimPrefix(segment, "f"))
				if err != nil {
					panic(err)
				}
				if !strings.HasPrefix(segment, "f") || index < 0 || index >= kind.NumFields() {
					current = nil
				} else {
					current = kind.Field(index).Type()
				}
			case *types.Slice:
				if segment == "e" {
					current = kind.Elem()
				} else {
					current = nil
				}
			case *types.Array:
				if segment == "e" {
					current = kind.Elem()
				} else {
					current = nil
				}
			default:
				current = nil
			}
			if current == nil {
				break
			}
		}
		if isError(current) {
			result[path] = true
		}
	}
	return result
}

func isError(value types.Type) bool {
	return value != nil && types.AssignableTo(value, types.Universe.Lookup("error").Type())
}

func isNil(value ssa.Value) bool {
	constant, ok := value.(*ssa.Const)
	return ok && constant.IsNil()
}

func (scanner *scan) evaluate(function *ssa.Function, start *ssa.BasicBlock, initial state) ([]paths, bool) {
	incoming := map[*ssa.BasicBlock]state{start: initial}
	queue := []*ssa.BasicBlock{start}
	for len(queue) != 0 {
		block := queue[0]
		queue = queue[1:]
		current := incoming[block].clone()
		for _, instruction := range block.Instrs {
			scanner.step(instruction, current)
		}
		for _, successor := range block.Succs {
			next := current.clone()
			for _, instruction := range successor.Instrs {
				phi, ok := instruction.(*ssa.Phi)
				if !ok {
					break
				}
				for i, predecessor := range successor.Preds {
					if predecessor == block {
						next.put(phi, current.get(phi.Edges[i]))
					}
				}
			}
			prior, exists := incoming[successor]
			if !exists {
				incoming[successor] = next
				queue = append(queue, successor)
			} else if merge(&prior, next) {
				incoming[successor] = prior
				queue = append(queue, successor)
			}
		}
	}
	var results []paths
	returned, safe := false, true
	for block, initial := range incoming {
		current := initial.clone()
		for _, instruction := range block.Instrs {
			scanner.step(instruction, current)
			if _, panics := instruction.(*ssa.Panic); panics && !current.captured {
				safe = false
			}
			if _, calls := instruction.(*ssa.Call); calls && len(block.Succs) == 0 && !returns(block) && !current.captured {
				safe = false
			}
			ret, ok := instruction.(*ssa.Return)
			if !ok {
				continue
			}
			retained := current.captured
			if results == nil {
				results = make([]paths, len(ret.Results))
			}
			for i, expression := range ret.Results {
				flow := retainedPaths(current.get(expression), expression.Type())
				retained = retained || len(flow) != 0
				if !returned {
					results[i] = copyPaths(flow)
				} else {
					intersect(results[i], flow)
				}
			}
			safe = safe && retained
			returned = true
		}
	}
	return results, returned && safe
}

func returns(block *ssa.BasicBlock) bool {
	for _, instruction := range block.Instrs {
		if _, ok := instruction.(*ssa.Return); ok {
			return true
		}
	}
	return false
}

func (scanner *scan) step(instruction ssa.Instruction, current state) {
	switch instruction := instruction.(type) {
	case *ssa.Store:
		root, prefix := address(instruction.Addr)
		current.store(root, prefix, current.get(instruction.Val))
	case *ssa.UnOp:
		if instruction.Op == token.MUL {
			current.put(instruction, current.get(instruction.X))
		}
	case *ssa.Field:
		current.put(instruction, subset(current.get(instruction.X), ".f"+strconv.Itoa(instruction.Field)))
	case *ssa.Index:
		current.put(instruction, subset(current.get(instruction.X), ".e"))
	case *ssa.Slice:
		if instruction.Low != nil || instruction.High != nil || instruction.Max != nil {
			root, prefix := address(instruction.X)
			current.store(root, prefix, nil)
			current.values[instruction] = paths{}
		} else {
			current.put(instruction, current.get(instruction.X))
		}
	case *ssa.MakeInterface:
		current.put(instruction, current.get(instruction.X))
	case *ssa.ChangeInterface:
		current.put(instruction, current.get(instruction.X))
	case *ssa.ChangeType:
		current.put(instruction, current.get(instruction.X))
	case *ssa.Extract:
		current.put(instruction, subset(current.get(instruction.Tuple), ".r"+strconv.Itoa(instruction.Index)))
	case *ssa.Call:
		scanner.call(instruction, current)
	}
}

func (scanner *scan) call(call *ssa.Call, current state) {
	common := call.Common()
	if builtin, ok := common.Value.(*ssa.Builtin); ok && builtin.Name() == "append" {
		flow := copyPaths(current.get(common.Args[0]))
		for path := range current.get(common.Args[1]) {
			flow[path] = true
		}
		current.put(call, flow)
		return
	}
	callee := common.StaticCallee()
	if callee == nil {
		current.invalidate(common.Args)
		return
	}
	function, _ := callee.Object().(*types.Func)
	if astutil.IsCaptureFunction(function) {
		for _, argument := range common.Args {
			if len(retainedPaths(current.get(argument), argument.Type())) != 0 {
				current.captured = true
			}
		}
		return
	}
	if function != nil && function.Pkg() != nil && function.Pkg().Path() == "fmt" && function.Name() == "Errorf" && len(common.Args) == 2 {
		format, ok := common.Args[0].(*ssa.Const)
		if ok && format.Value != nil && format.Value.Kind() == constant.String && singleWrapFormat(constant.StringVal(format.Value)) {
			if retained := current.get(common.Args[1]); retained[".e"] && soleArgument(common.Args[1]) {
				current.put(call, paths{"": true})
			}
		}
		return
	}
	if len(callee.Blocks) == 0 {
		current.invalidate(common.Args)
		return
	}
	flow := paths{}
	for i, argument := range common.Args {
		if i >= len(callee.Params) || !isError(callee.Params[i].Type()) || len(current.get(argument)) == 0 {
			continue
		}
		for result, retained := range scanner.summary(constructor{callee, i}) {
			prefix := ""
			if callee.Signature.Results().Len() > 1 {
				prefix = ".r" + strconv.Itoa(result)
			}
			for path := range retained {
				flow[prefix+path] = true
			}
		}
	}
	current.put(call, flow)
	current.invalidate(common.Args)
}

func singleWrapFormat(format string) bool {
	wrapping := 0
	for index := 0; index < len(format); index++ {
		if format[index] != '%' {
			continue
		}
		index++
		if index >= len(format) {
			return false
		}
		if format[index] == '%' {
			continue
		}
		if format[index] != 'w' {
			return false
		}
		wrapping++
	}
	return wrapping == 1
}

func soleArgument(value ssa.Value) bool {
	slice, ok := value.(*ssa.Slice)
	if !ok {
		return false
	}
	pointer, ok := slice.X.Type().Underlying().(*types.Pointer)
	if !ok {
		return false
	}
	array, ok := pointer.Elem().Underlying().(*types.Array)
	return ok && array.Len() == 1
}

func (current state) invalidate(arguments []ssa.Value) {
	for _, argument := range arguments {
		switch argument.Type().Underlying().(type) {
		case *types.Pointer, *types.Slice, *types.Map:
			if isError(argument.Type()) {
				continue
			}
			root, prefix := address(argument)
			current.store(root, prefix, nil)
		}
	}
}

func (scanner *scan) summary(key constructor) []paths {
	if result, found := scanner.summaries[key]; found {
		return result
	}
	if scanner.active[key] {
		return []paths{}
	}
	scanner.active[key] = true
	initial := newState()
	initial.values[key.function.Params[key.parameter]] = paths{"": true}
	initial.scalars[key.function.Params[key.parameter]] = true
	results, _ := scanner.evaluate(key.function, key.function.Blocks[0], initial)
	delete(scanner.active, key)
	scanner.summaries[key] = results
	return results
}
