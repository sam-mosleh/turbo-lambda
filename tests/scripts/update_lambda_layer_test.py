from pathlib import Path

from turbo_lambda.scripts.update_turbo_lambda_layer import main


def test_multiple_files(tmp_path: Path) -> None:
    before_text = "a \nx arn:aws:lambda:us-east-1:1:layer:turbo_lambda-0-1-2-arm64-python313:1 x\n b"
    after_text = "a \nx arn:aws:lambda:us-east-1:099532377432:layer:turbo_lambda-1-2-3-arm64-python313:1 x\n b"
    valid_file = tmp_path / "valid_file.txt"
    valid_file.write_text(before_text)
    empty_file = tmp_path / "empty_file.txt"
    empty_file.write_text("")
    assert main("1.2.3", [str(valid_file), str(empty_file)]) == 1
    assert valid_file.read_text() == after_text


def test_non_existing_file(tmp_path: Path) -> None:
    assert main("2.3.4", [str(tmp_path / "non_existing_file.txt")]) == 0
