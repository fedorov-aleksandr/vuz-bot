import csv
import json
import re


def _normalize_key(key: str) -> str:
    key = key.strip()
    key = key.replace(' ', '_')
    return key.lower()


def _normalize_subject_name(s: str) -> str:
    s = s.strip().lower()
    s = re.sub(r"[\s\-]+", "_", s)
    # common typo
    if 'иностар' in s or 'иностран' in s or 'иностра' in s:
        return 'иностранный_язык'
    # map common names to internal keys
    mapping = {
        'математика': 'математика',
        'русский': 'русский',
        'информатика': 'информатика',
        'физика': 'физика',
        'биология': 'биология',
        'химия': 'химия',
        'география': 'география',
        'литература': 'литература',
        'история': 'история',
        'общество': 'общество',
        'иностранный_язык': 'иностранный_язык'
    }
    return mapping.get(s, s.replace(' ', '_'))


def _to_int(value):
    try:
        v = str(value).strip()
        if v == '':
            return 0
        return int(float(v))
    except Exception:
        return 0


def convert_csv_to_json(csv_file, json_file):
    data = []
    with open(csv_file, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            # Пропустить полностью пустые строки
            if all(not (v and str(v).strip()) for v in row.values()):
                continue
            norm = {}
            for k, v in row.items():
                if v is None:
                    v = ''
                key = _normalize_key(k)
                val = v.strip()
                # Типизация для числовых полей
                if 'мин' in key or 'проходной' in key or key.endswith('_балл') or key.endswith('_сумма'):
                    norm[key] = _to_int(val)
                else:
                    norm[key] = val
            # Нормализовать поле с предметами, если есть
            if 'предметы_список' in norm:
                subj_raw = norm['предметы_список']
                parts = [p.strip() for p in re.split(r',', subj_raw) if p.strip()]
                normalized_parts = []
                for p in parts:
                    if '/' in p:
                        alts = [a.strip() for a in p.split('/') if a.strip()]
                        normalized_parts.append('/'.join(_normalize_subject_name(a) for a in alts))
                    else:
                        normalized_parts.append(_normalize_subject_name(p))
                norm['предметы_список'] = ', '.join(normalized_parts)

            data.append(norm)

    with open(json_file, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=4)


if __name__ == "__main__":
    convert_csv_to_json('vuz_rate - Sheet1.csv', 'vuz_data.json')