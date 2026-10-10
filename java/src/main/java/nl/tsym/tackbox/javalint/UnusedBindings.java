package nl.tsym.tackbox.javalint;

import com.github.javaparser.ast.CompilationUnit;
import com.github.javaparser.ast.body.MethodDeclaration;
import com.github.javaparser.ast.body.TypeDeclaration;
import com.github.javaparser.ast.expr.MethodCallExpr;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import net.sourceforge.pmd.PMDConfiguration;
import net.sourceforge.pmd.PmdAnalysis;
import net.sourceforge.pmd.lang.document.FileId;
import net.sourceforge.pmd.lang.document.TextFile;
import net.sourceforge.pmd.lang.java.JavaLanguageModule;
import net.sourceforge.pmd.lang.java.rule.bestpractices.UnusedPrivateFieldRule;
import net.sourceforge.pmd.lang.java.rule.bestpractices.UnusedPrivateMethodRule;
import net.sourceforge.pmd.lang.java.rule.codestyle.UnnecessaryImportRule;
import net.sourceforge.pmd.lang.rule.Rule;
import net.sourceforge.pmd.lang.rule.RuleSet;
import net.sourceforge.pmd.reporting.Report;
import net.sourceforge.pmd.reporting.RuleViolation;
import net.sourceforge.pmd.util.log.PmdReporter;

final class UnusedBindings {

    private UnusedBindings() {}

    static List<Finding> check(String file, String content, CompilationUnit cu) {
        PMDConfiguration config = new PMDConfiguration();
        config.setClassLoader(ClassLoader.getPlatformClassLoader());
        config.setThreads(0);
        config.setIgnoreIncrementalAnalysis(true);
        config.setReporter(PmdReporter.quiet());
        try (PmdAnalysis pmd = PmdAnalysis.create(config)) {
            pmd.addRuleSet(RuleSet.create("unused-bindings", "Unused Java bindings", null,
                    List.of(), List.of(), List.of(
                            rule(new UnusedLocalBindingsRule(), "JV011", "unused local variable ''{0}''"),
                            rule(new UnusedPrivateFieldRule(), "JV012", "unused private field ''{0}''"),
                            rule(new UnusedPrivateMethodRule(), "JV013", "unused private method ''{0}''"),
                            rule(new UnnecessaryImportRule(), "JV014", null))));
            if (!pmd.files().addFile(TextFile.forCharSeq(content, FileId.fromPathLikeString(file),
                    JavaLanguageModule.getInstance().getDefaultVersion()))) {
                throw new IllegalStateException("PMD did not accept " + file);
            }
            Report report = pmd.performAnalysisAndCollectReport();
            if (!report.getProcessingErrors().isEmpty()) {
                Report.ProcessingError error = report.getProcessingErrors().get(0);
                throw new IllegalArgumentException("PMD cannot analyze " + file + ": " + error.getMsg(),
                        error.getError());
            }
            if (!report.getConfigurationErrors().isEmpty()) {
                throw new IllegalStateException("PMD configuration error for " + file + ": "
                        + report.getConfigurationErrors().get(0).issue());
            }
            if (pmd.getReporter().numErrors() != 0) {
                throw new IllegalStateException("PMD failed to analyze " + file);
            }
            List<Finding> findings = new ArrayList<>();
            for (RuleViolation violation : report.getViolations()) {
                addFinding(findings, file, violation, cu);
            }
            // PMD suppressions cannot bypass the unused-bindings lane.
            for (Report.SuppressedViolation suppressed : report.getSuppressedViolations()) {
                addFinding(findings, file, suppressed.getRuleViolation(), cu);
            }
            findings.sort(Comparator.comparingInt(Finding::line)
                    .thenComparingInt(Finding::column).thenComparing(Finding::rule));
            return findings;
        }
    }

    private static Rule rule(Rule rule, String id, String message) {
        rule.setLanguage(JavaLanguageModule.getInstance());
        rule.setName(id);
        rule.setMessage(message);
        return rule;
    }

    private static void addFinding(List<Finding> findings, String file, RuleViolation violation, CompilationUnit cu) {
        String id = violation.getRule().getName();
        if (id.equals("JV013") && possiblyCalledOverload(violation, cu)) {
            return;
        }
        // Without a consumer classpath, wildcard suppliers cannot be resolved reliably.
        if (id.equals("JV014") && violation.getDescription().startsWith("Unused")
                && cu.getImports().stream().anyMatch(i -> i.isAsterisk()
                        && i.getBegin().orElseThrow().line == violation.getBeginLine()
                        && i.getBegin().orElseThrow().column == violation.getBeginColumn())) {
            return;
        }
        findings.add(new Finding(id, file, violation.getBeginLine(), violation.getBeginColumn(),
                violation.getEndLine(), violation.getEndColumn(), id + ": " + violation.getDescription()));
    }

    private static boolean possiblyCalledOverload(RuleViolation violation, CompilationUnit cu) {
        String name = violation.getAdditionalInfo().get(RuleViolation.METHOD_NAME);
        for (MethodDeclaration method : cu.findAll(MethodDeclaration.class)) {
            if (!method.getNameAsString().equals(name)
                    || !method.getRange().orElseThrow().contains(new com.github.javaparser.Position(
                            violation.getBeginLine(), violation.getBeginColumn()))) {
                continue;
            }
            TypeDeclaration<?> owner = method.findAncestor(TypeDeclaration.class).orElseThrow();
            long sameName = owner.getMethods().stream().filter(m -> m.getNameAsString().equals(name)).count();
            if (sameName > 1 && cu.findAll(MethodCallExpr.class).stream().anyMatch(call ->
                    call.getNameAsString().equals(name)
                            && call.getArguments().size() == method.getParameters().size())) {
                return true;
            }
        }
        return false;
    }
}
