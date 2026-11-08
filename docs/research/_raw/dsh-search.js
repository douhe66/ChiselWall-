/** 查询侧 grams：clean（去空白标点、ASCII 小写）后按位 3-gram（实验管线口径）。 */
export function queryGrams(text) {
    const clean = String(text).toLowerCase().replace(/[\s\p{P}\p{S}]+/gu, '');
    const out = [];
    for (let i = 0; i + 3 <= clean.length; i++)
        out.push(clean.slice(i, i + 3));
    return [...new Set(out)];
}
/** 文档侧 grams：FTS5 trigram 风格（token 内 3-gram，非字母数字切分，ASCII 小写）。 */
export function docGrams(text) {
    const out = [];
    for (const token of String(text).toLowerCase().split(/[^\p{L}\p{N}]+/u)) {
        for (let i = 0; i + 3 <= token.length; i++)
            out.push(token.slice(i, i + 3));
    }
    return out;
}
/**
 * 构造 FTS5 MATCH 查询：grams 全部 OR 化（长查询必须 OR；quoted 单 gram 是子串语义）。
 * @param grams 查询 gram 集合（已去重）
 */
export function matchOf(grams) {
    return grams.map((g) => `"${g}"`).join(' OR ');
}
/**
 * gram 截断：生产 maxQueryTrigrams 封顶时按 idf 降序保留（顺序截断会丢失长查询
 * 判别词汇——原型实测 human A 65%→41%；idf 高的 gram 更稀有、判别力更强）。
 * @param grams 查询 gram 集合
 * @param dfOf gram → 包含它的文档数（本 workspace 可见文档）
 * @param totalDocs 文档总数
 * @param cap 0 = 不限
 */
export function truncateGrams(grams, dfOf, totalDocs, cap) {
    if (cap <= 0 || grams.length <= cap)
        return grams;
    const idf = (g) => {
        const n = dfOf.get(g) ?? 0;
        return n <= 0 ? 0 : Math.log((totalDocs + 1) / (n + 1));
    };
    return [...grams]
        .sort((a, b) => idf(b) - idf(a))
        .slice(0, cap);
}
/** 命中片段：最优匹配 gram 附近 ±窗口（content 内）。 */
export function snippetOf(content, qgrams) {
    const lower = String(content).toLowerCase();
    let best = -1;
    for (const g of qgrams) {
        const i = lower.indexOf(g);
        if (i >= 0 && (best < 0 || i < best))
            best = i;
    }
    if (best < 0)
        return String(content).slice(0, 80);
    const start = Math.max(0, best - 12);
    const end = Math.min(content.length, best + 42);
    return (start > 0 ? '…' : '') + content.slice(start, end) + (end < content.length ? '…' : '');
}
/** 命中窗口来自原文（original）还是富化字段（enriched）。 */
export function provenanceOf(content, keywords, synonyms, questions, qgrams) {
    const lower = String(content).toLowerCase();
    if (qgrams.some((g) => lower.includes(g)))
        return 'original';
    const rich = [keywords, synonyms, questions].filter(Boolean).join(' ').toLowerCase();
    return rich.length > 0 && qgrams.some((g) => rich.includes(g)) ? 'enriched' : 'original';
}
/** 粗略 token 估算（正式版可换 ctx.tokenMeter 估算器；CJK+ASCII 混合 ~3 字符/token）。 */
export function estimateTokens(text) {
    return Math.ceil(String(text).length / 3);
}
/** djb2 哈希 → 8 位 hex（确定性 id 与 contentHash）。 */
export function djb2(s) {
    let h = 5381;
    for (let i = 0; i < s.length; i++)
        h = ((h << 5) + h + s.charCodeAt(i)) >>> 0;
    return h.toString(16).padStart(8, '0');
}
/** 归一化查询（L1 缓存键）。 */
export function normalizeQuery(q) {
    return String(q).trim().toLowerCase().replace(/\s+/g, ' ');
}
/** 富化文本拼接（rich 表索引内容）。 */
export function richTextOf(fields) {
    return [fields.content, fields.keywords, fields.synonyms, fields.questions].filter(Boolean).join(' ');
}
/** FTS 表名。 */
export function ftsTableOf(table) {
    return table === 'rich' ? 'items_fts_rich' : 'items_fts_base';
}
