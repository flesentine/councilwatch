from pathlib import Path


def replace_once(text, old, new, label):
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected 1 match, found {count}")
    return text.replace(old, new, 1)


pc_path = Path("process_city.py")
pc = pc_path.read_text(encoding="utf-8")

pc = replace_once(
    pc,
    '''            scoped_text = " ".join(
                [
                    str(story.headline or ""),
                    str(story.dek or ""),
                    value,
                ]
            )

            matching_context = next(
''',
    '''            if field == "headline":
                scoped_text = value

            elif field == "dek":
                scoped_text = " ".join(
                    [
                        str(story.headline or ""),
                        value,
                    ]
                )

            else:
                # Body paragraphs and key facts must identify the
                # conduit-financing item locally. Do not inherit
                # anchors from another topic in the headline/dek.
                scoped_text = value

            matching_context = next(
''',
    "story conduit local scoping",
)

pc_path.write_text(pc, encoding="utf-8")


test_path = Path("tests/test_rsm_sept9_regressions.py")
tests = test_path.read_text(encoding="utf-8")

tests = replace_once(
    tests,
    '            "The City debt issuance would total up to $10 million."\n',
    '            "The City debt issuance for St. Junipero Serra Catholic "\n'
    '            "School would total up to $10 million."\n',
    "conduit body fixture local identity",
)

test_path.write_text(tests, encoding="utf-8")

print("RSM story scoping fix applied.")
