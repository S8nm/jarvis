import { createElement, Fragment } from 'react';

/**
 * Lightweight markdown-to-React parser.
 * Handles: code blocks, inline code, bold, italic, line breaks.
 * No library dependency — pure regex.
 */
export function formatMessage(text) {
    if (!text) return null;

    // Split on code blocks first
    const parts = text.split(/(```[\s\S]*?```)/g);

    return createElement(Fragment, null, ...parts.map((part, i) => {
        // Code block
        if (part.startsWith('```') && part.endsWith('```')) {
            const inner = part.slice(3, -3);
            const newlineIdx = inner.indexOf('\n');
            const lang = newlineIdx > 0 && newlineIdx < 20 ? inner.slice(0, newlineIdx).trim() : '';
            const code = lang ? inner.slice(newlineIdx + 1) : inner;
            return createElement('pre', { key: i, className: 'msg-code-block' },
                createElement('code', { className: lang ? `lang-${lang}` : undefined }, code)
            );
        }

        // Inline formatting
        return createElement(Fragment, { key: i }, ...formatInline(part));
    }));
}

function formatInline(text) {
    // Split by inline patterns: **bold**, `code`, *italic*, newlines
    const tokens = [];
    const regex = /(\*\*[^*]+\*\*|`[^`]+`|\*[^*]+\*|\n)/g;
    let lastIndex = 0;
    let match;

    while ((match = regex.exec(text)) !== null) {
        if (match.index > lastIndex) {
            tokens.push(text.slice(lastIndex, match.index));
        }
        const m = match[0];
        if (m === '\n') {
            tokens.push(createElement('br', { key: `br-${match.index}` }));
        } else if (m.startsWith('**') && m.endsWith('**')) {
            tokens.push(createElement('strong', { key: `b-${match.index}` }, m.slice(2, -2)));
        } else if (m.startsWith('`') && m.endsWith('`')) {
            tokens.push(createElement('code', { key: `c-${match.index}`, className: 'msg-inline-code' }, m.slice(1, -1)));
        } else if (m.startsWith('*') && m.endsWith('*')) {
            tokens.push(createElement('em', { key: `i-${match.index}` }, m.slice(1, -1)));
        }
        lastIndex = match.index + m.length;
    }

    if (lastIndex < text.length) {
        tokens.push(text.slice(lastIndex));
    }

    return tokens.length ? tokens : [text];
}
