// Keep in sync with src/name_cleaning.py; covered by cross-language fixtures.
const jeEndingWords = ['강제', '거제', '김제', '연제', '인제', '지제', '해제', '홍제', '효제', '백제', '황제', '국제', '경제', '형제'];
export function cleanApartmentName(value) {
  let text = String(value ?? '').normalize('NFKC');
  let previous;
  while (text !== previous) {
    previous = text;
    text = text.replace(/\([^()]*\)|\[[^\[\]]*\]|\{[^{}]*\}/gu, (group) => {
      const inside = group.slice(1, -1);
      return /[0-9]/u.test(inside) && /^[0-9제차단지블록동호층번지\s,·;:/~\-–—.ㆍ]+$/u.test(inside) ? ' ' : group;
    });
    text = text.replace(/(?:제\s*)?[0-9]+(?:\s*[,·.ㆍ/&~–—-]\s*[0-9]+)*\s*(?:단지|블록|번지|차|동|호|층)/gu, (group, offset, whole) => group.startsWith('제') && jeEndingWords.some(word => whole.slice(0, offset + 1).endsWith(word)) ? '제 ' : ' ');
    text = text.replace(/(?<![\p{L}\p{N}_.])[0-9]+(?:\s*[-~–—]\s*[0-9]+)*(?![\p{L}\p{N}_.])/gu, ' ');
    text = text.replace(/\s+/gu, ' ').trim();
    text = text.replace(/(?<=[가-힣)\]}])[0-9]+$/gu, '');
    text = text.replace(/([([{])\s*[,·;:/~\-–—.ㆍ]*\s*/gu, '$1');
    text = text.replace(/\s*[,·;:/~\-–—.ㆍ]*\s*([)\]}])/gu, '$1');
    text = text.replace(/\(\s*\)|\[\s*\]|\{\s*\}/gu, ' ');
    text = text.replace(/\s+/gu, ' ').replace(/^[ ,·;:/~\-–—.ㆍ]+|[ ,·;:/~\-–—.ㆍ]+$/gu, '');
  }
  return text;
}
