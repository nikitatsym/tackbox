// Custom markdownlint rule: flag any character outside the printable
// ASCII range (codepoints > 0x7F). Keeps docs strictly ASCII so
// em-dashes, curly quotes, Cyrillic, box-drawing chars, emoji, etc.
// cannot leak into prose, tables, or code fences.

module.exports = {
  names: ['MD-ASCII', 'no-non-ascii'],
  description: 'Non-ASCII character',
  tags: ['ascii'],
  parser: 'none',
  function: function rule(params, onError) {
    params.lines.forEach((line, idx) => {
      let col = 0
      for (const ch of line) {
        const code = ch.codePointAt(0)
        if (code > 0x7f) {
          onError({
            lineNumber: idx + 1,
            detail: 'Non-ASCII character U+' + code.toString(16).toUpperCase() + ' (' + ch + ')',
            range: [col + 1, ch.length],
          })
        }
        col += ch.length
      }
    })
  },
}
