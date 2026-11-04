"""Atlas-declared form links shared by portrait and NP maintenance."""
import re


def walk(value):
    yield value
    if isinstance(value, dict):
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


def transformed_ids(record):
    result = set()
    for node in walk(record):
        if not isinstance(node, dict) or node.get('funcType') != 'transformServant':
            continue
        for key, value in node.items():
            if re.fullmatch(r'svals[0-9]*', key):
                for row in value:
                    sid = row.get('Value')
                    if type(sid) is not int or sid <= 0:
                        raise ValueError('Invalid transformServant target')
                    result.add(str(sid))
    return result
