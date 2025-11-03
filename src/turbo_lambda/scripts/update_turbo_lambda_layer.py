import re
import sys
from typing import TextIO

from turbo_lambda.version import __version__


def main(version: str = __version__, input_io: TextIO = sys.stdin) -> int:
    dashed_version = version.replace(".", "-")
    pattern = re.compile(
        r"arn:aws:lambda:(.*):\d+:layer:turbo_lambda-\d+-\d+-\d+-(.*)-(.*):1"
    )
    replacement = (
        rf"arn:aws:lambda:\1:099532377432:layer:turbo_lambda-{dashed_version}-\2-\3:1"
    )
    filenames = [filename.strip() for filename in input_io]
    for filename in filenames:
        try:
            with open(filename) as fp:
                content = fp.read()
        except FileNotFoundError:
            continue
        matches = pattern.findall(content)
        if not matches:
            continue
        new_content = pattern.sub(replacement, content)
        with open(filename, "w") as fp:
            fp.write(new_content)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
