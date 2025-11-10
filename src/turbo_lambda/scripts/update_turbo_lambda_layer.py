import argparse
import re
from collections.abc import Sequence

from turbo_lambda.version import __version__


def main(version: str = __version__, argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Update all the turbo-lambda layer versions"
    )
    parser.add_argument("filenames", nargs="*")
    args = parser.parse_args(argv)
    dashed_version = version.replace(".", "-")
    pattern = re.compile(
        r"arn:aws:lambda:(.*):\d+:layer:turbo_lambda-\d+-\d+-\d+-(.*)-(.*):1"
    )
    replacement = (
        rf"arn:aws:lambda:\1:099532377432:layer:turbo_lambda-{dashed_version}-\2-\3:1"
    )
    exit_code = 0
    for filename in args.filenames:
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
        exit_code = 1
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
