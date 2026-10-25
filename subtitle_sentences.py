"""Sentence-first subtitle grouping without changing stored translation IDs."""
import re

LINE_WIDTH = 48  # Latin units; Chinese/Japanese glyphs count as two.


def text_width(text):
    return sum(2 if '\u3000' <= c <= '\u9fff' else 1 for c in text)


def short_parts(text, limit=LINE_WIDTH):
    tokens = re.findall(r'[\u3000-\u9fff]|[^\s\u3000-\u9fff]+', text)
    parts, current = [], ''
    for token in tokens:
        # Very long URLs/words also need a safe display limit, without truncation.
        pieces = [token[i:i+limit] for i in range(0, len(token), limit)]
        for piece in pieces:
            joined = join_text(current, piece)
            if current and (text_width(joined) > limit or sentence_end(current) or
                            (current.endswith((',', ';', ':', '，', '；', '：')) and text_width(current) >= limit / 2)):
                parts.append(current)
                current = piece
            else:
                current = joined
    if current:
        parts.append(current)
    return parts


def display_rows(rows, merge=True):
    """Presentation-only splitting leaves source IDs and translation caches intact.

    Legacy rows lack word timing: their split times are proportional estimates.
    New recognition already emits short rows using actual word timestamps.
    """
    output = []
    for row in (sentence_rows(rows) if merge else rows):
        original = short_parts(row['text'])
        translated = short_parts(row.get('translation', ''))
        count = max(len(original), len(translated), 1)
        if count == 1:
            output.append({**row, 'text': original[0] if original else '',
                           'translation': translated[0] if translated else ''})
            continue
        # Balance both languages over the same number of display intervals;
        # do not put all of a short translation on only the first interval.
        def balance(text, parts):
            if len(parts) == count or not text:
                return parts
            tokens = re.findall(r'[\u3000-\u9fff]|[^\s\u3000-\u9fff]+', text)
            result, position = [], 0
            for i in range(count):
                stop = round(len(tokens) * (i + 1) / count)
                value = ''
                for token in tokens[position:stop]:
                    value = join_text(value, token)
                result.append(value)
                position = stop
            if all(text_width(value) <= LINE_WIDTH for value in result):
                return result
            return parts
        original = balance(row['text'], original)
        translated = balance(row.get('translation', ''), translated)
        weights = [max(text_width(original[i]) if i < len(original) else 0,
                       text_width(translated[i]) if i < len(translated) else 0, 1) for i in range(count)]
        total, elapsed = sum(weights), 0
        for i, weight in enumerate(weights):
            start = row['start'] + (row['end'] - row['start']) * elapsed / total
            elapsed += weight
            output.append({**row, 'id': f"{row['id']}:{i}", 'source_id': row['id'],
                           'start': start, 'end': row['start'] + (row['end'] - row['start']) * elapsed / total,
                           'text': original[i] if i < len(original) else '',
                           'translation': translated[i] if i < len(translated) else '',
                           'translation_complete': bool(row.get('translation')),
                           'timing_estimated': True})
    return output


def sentence_end(text):
    text = text.rstrip().rstrip('\"\'”’»」』）)')
    if not text.endswith(('.', '?', '!', '。', '？', '！', '…')):
        return False
    if re.search(r'\b(?:Mr|Mrs|Ms|Dr|Prof|Sr|Jr|St|vs|etc)\.$', text, re.I):
        return False
    if re.search(r'(?:\b[A-Za-z]\.){1,}$', text):
        return False
    return True


def join_text(left, right):
    left, right = left.rstrip(), right.lstrip()
    if not left or not right:
        return left + right
    cjk = lambda c: '\u3040' <= c <= '\u9fff'
    if right[0] in ',.;:?!，。；：？！…、）)]”’»' or cjk(left[-1]) or cjk(right[0]):
        return left + right
    return left + ' ' + right


def sentence_rows(rows):
    """Keep actual boundaries; never bridge silence or pair partial translations."""
    output = []
    current = None
    for row in rows:
        row = row.copy()
        if current is None:
            current = row
            continue
        text = join_text(current['text'], row['text'])
        limit = LINE_WIDTH
        hard_boundary = (row['start'] - current['end'] > 1 or
                         row['end'] - current['start'] > 6 or text_width(text) > limit or
                         text_width(join_text(current.get('translation', ''), row.get('translation', ''))) > limit)
        soft_boundary = (current['text'].rstrip().endswith((',', ';', ':', '，', '；', '：'))
                         and (text_width(current['text']) > limit * 0.5 or current['end'] - current['start'] > 3))
        translation_boundary = ('translation' in row and
                                bool(row['translation']) != bool(current.get('translation')))
        if sentence_end(current['text']) or hard_boundary or soft_boundary or translation_boundary:
            output.append(current)
            current = row
        else:
            if 'id' in current and 'id' in row:
                current['source_ids'] = current.get('source_ids', [current['id']]) + row.get('source_ids', [row['id']])
            current['text'] = text
            current['end'] = max(current['end'], row['end'])
            if 'translation' in row:
                current['translation'] = join_text(current.get('translation', ''), row['translation'])
    if current is not None:
        output.append(current)
    return output
