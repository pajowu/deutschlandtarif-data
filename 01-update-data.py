from typing import final
import fitz
import argparse
import csv
import pathlib
import tqdm
import requests
from bs4 import BeautifulSoup
from tqdm.contrib.concurrent import process_map
import itertools

HEADER_REPLACEMENTS = {"Regio n": "Region"}


def combine_tables(tables):
    final_table = tables[0]
    for table in tables[1:]:
        assert table[0] == final_table[0]
        final_table.extend(table[1:])
    return final_table


def extract_page_table(file_content, page):
    doc = fitz.Document(stream=file_content)
    return [table.extract() for table in doc[page].find_tables().tables]


def cleanup_headers(table):
    headers = table[0]
    for i, header in enumerate(headers):
        header = " ".join(header.splitlines()).replace("- ", "")
        header = HEADER_REPLACEMENTS.get(header, header)
        headers[i] = header
    table[0] = headers
    return table


def extract_table(file_content):
    doc = fitz.Document(stream=file_content)
    num_pages = doc.page_count

    tables = process_map(
        extract_page_table,
        itertools.repeat(file_content),
        range(num_pages),
        total=num_pages,
        chunksize=1,
    )
    final_table = combine_tables(list(itertools.chain.from_iterable(tables)))
    final_table = cleanup_headers(final_table)
    return final_table


def extract_pdf_table(file, output_file):
    table = extract_table(file)
    with open(output_file, "w") as f:
        csv.writer(f).writerows(table)


def extract_pdf_links():
    req = requests.get("https://bahn.de/agb")
    req.raise_for_status()
    soup = BeautifulSoup(req.text)
    deutschlandtarif_heading = soup.find(
        "h2", string="Entfernungswerk des Deutschlandtarifs"
    )
    assert deutschlandtarif_heading is not None
    assert deutschlandtarif_heading.parent is not None
    for link in deutschlandtarif_heading.parent.find_all("a")[1:]:
        assert link.span
        yield link.attrs["href"], link.span.text.split()[0]


def download_if_modified(url, if_modified_since=None):
    headers = {}
    if if_modified_since is not None:
        headers["If-Modified-Since"] = last_modified_data[name]

    req = requests.get(url, stream=True, headers=headers)
    req.raise_for_status()
    if req.status_code == 304:
        return None, None

    total = int(req.headers.get("content-length", 0))
    res = bytes()
    with tqdm.tqdm(
        total=total,
        unit="iB",
        unit_scale=True,
        unit_divisor=1024,
    ) as bar:
        for data in req.iter_content(chunk_size=1024):
            res += data
            bar.update(len(data))
    return res, req.headers["last-modified"]


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=pathlib.Path)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    args.output.mkdir(exist_ok=True)

    last_modified_data = {}
    last_modified_csv = args.output / "last_modified.csv"
    if last_modified_csv.exists():
        with open(last_modified_csv) as f:
            reader = csv.DictReader(f)
            for row in reader:
                last_modified_data[row["name"]] = row["date"]

    links = list(extract_pdf_links())
    for url, name in links:
        print("Downloading", name)
        last_mod = last_modified_data.get(name) if not args.force else None
        content, last_modified = download_if_modified(url, if_modified_since=last_mod)
        if content is None:
            print(name, "was not modified, skipping")
            continue
        print("Extracting data from", name)
        extract_pdf_table(content, args.output / f"{name}.csv")

        last_modified_data[name] = last_modified

    with open(last_modified_csv, "w") as f:
        writer = csv.DictWriter(f, fieldnames=["name", "date"])
        writer.writeheader()
        writer.writerows({"name": k, "date": v} for k, v in last_modified_data.items())
