import glob
import csv
import json
import argparse
from pathlib import Path

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()

    args.output.mkdir(exist_ok=True)

    for file in args.input.glob("*.csv"):
        output_file = args.output / file.with_suffix(".json").name
        with open(file) as input_file, open(output_file, "w") as output_file:
            reader = csv.DictReader(input_file)
            json.dump(list(reader), output_file)
