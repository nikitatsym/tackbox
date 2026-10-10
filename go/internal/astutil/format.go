package astutil

// WrappingArguments selects the operands of %w, including explicit indexes.
// Dynamic widths consume arguments but are not themselves wrapping operands.
func WrappingArguments(format string) []int {
	var wrapped []int
	next := 0
	for offset := 0; offset < len(format); offset++ {
		if format[offset] != '%' {
			continue
		}
		offset++
		if offset >= len(format) {
			return []int{}
		}
		if offset < len(format) && format[offset] == '%' {
			continue
		}
		for offset < len(format) {
			char := format[offset]
			if char == '[' {
				end := offset + 1
				for end < len(format) && format[end] != ']' {
					end++
				}
				if end == len(format) {
					return []int{}
				}
				index := 0
				for _, digit := range format[offset+1 : end] {
					if digit < '0' || digit > '9' || index > len(format) {
						return []int{}
					}
					index = index*10 + int(digit-'0')
				}
				if index < 1 {
					return []int{}
				}
				next = index - 1
				offset = end + 1
				continue
			}
			if char == '*' {
				next++
				offset++
				continue
			}
			if char == '#' || char == '0' || char == '+' || char == '-' || char == ' ' || char == '.' || char >= '1' && char <= '9' {
				offset++
				continue
			}
			if char == 'w' {
				wrapped = append(wrapped, next)
			}
			if char != '%' {
				next++
			}
			break
		}
	}
	return wrapped
}
