from typing import final
import argparse
import csv
import pathlib
import tqdm
import requests
from bs4 import BeautifulSoup
from tqdm.contrib.concurrent import process_map
import itertools
import time
import pymupdf
import pymupdf4llm
import pycmarkgfm

HEADER_REPLACEMENTS = {"Regio n": "Region"}


def combine_tables(tables):
    final_table = tables[0]
    for table in tables[1:]:
        assert table[0] == final_table[0]
        final_table.extend(table[1:])
    return final_table


def extract_page_table(file_content, page):
    doc = pymupdf.Document(stream=file_content)
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
    doc = pymupdf.Document(stream=file_content)
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


def extract_pdf_table(file, output_dir, name):
    table = extract_table(file)
    with open(output_dir / f"{name}.csv", "w") as f:
        csv.writer(f).writerows(table)


def extract_pdf_links(heading, skip):
    req = get_checked("https://bahn.de/agb")
    soup = BeautifulSoup(req.text)
    deutschlandtarif_heading = soup.find("h2", string=heading)
    assert deutschlandtarif_heading is not None
    assert deutschlandtarif_heading.parent is not None
    for link in deutschlandtarif_heading.parent.find_all("a")[skip:]:
        assert link.span
        yield link.attrs["href"], link.span.text.split()[0]


def get_checked(*args, **kwargs):
    req = requests.get(*args, **kwargs)
    while req.status_code == 429:
        time.sleep(5)
        req = requests.get(*args, **kwargs)

    req.raise_for_status()
    return req


def download_if_modified(url, if_modified_since=None):
    headers = {}
    if if_modified_since is not None:
        headers["If-Modified-Since"] = last_modified_data[name]

    req = get_checked(url, stream=True, headers=headers)
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


def parse_bb(content, output_dir, _name):
    doc = pymupdf.Document(stream=content)
    with open(output_dir / "Beförderungsbedingungen_raw.html", "w") as f:
        for page in doc:
            f.write(page.get_text("xhtml"))

    chunks = pymupdf4llm.to_markdown(
        doc, table_strategy="lines_strict", show_progress=True, page_chunks=True
    )
    md_text = ""
    for i, chunk in enumerate(chunks, 1):
        md_text += (
            chunk["text"].replace(f"\n\n{i}\n\n", "").replace(f"\n\n[{i}]\n\n", "")
        )
    with open(output_dir / "Beförderungsbedingungen_tables.md", "w") as f:
        f.write(md_text)

    with open(output_dir / "Beförderungsbedingungen_tables.html", "w") as f:
        f.write(
            pycmarkgfm.gfm_to_html(
                md_text,
                options=pycmarkgfm.options.hardbreaks | pycmarkgfm.options.unsafe,
            )
        )


def extract_pdf_text(content, output_dir, name):
    doc = pymupdf.Document(stream=content)
    with open(output_dir / f"{name}_raw.html", "w") as f:
        for page in doc:
            f.write(
                page.get_text(
                    "xhtml",
                    flags=pymupdf.TEXTFLAGS_XHTML & ~pymupdf.TEXT_PRESERVE_IMAGES,
                )
            )
    md_text = pymupdf4llm.to_markdown(doc, ignore_images=True)
    md_text = md_text.replace("DB Intern / DB internal", "")

    with open(output_dir / f"{name}_tables.md", "w") as f:
        f.write(md_text)

    with open(output_dir / f"{name}_tables.html", "w") as f:
        f.write(
            pycmarkgfm.gfm_to_html(
                md_text,
                options=pycmarkgfm.options.hardbreaks | pycmarkgfm.options.unsafe,
            )
        )


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

    links = (
        list(
            zip(
                extract_pdf_links("Entfernungswerk des Deutschlandtarifs", 1),
                itertools.repeat(extract_pdf_table),
            )
        )
        + [
            (
                next(extract_pdf_links("Beförderungsbedingungen Personenverkehr", 0)),
                parse_bb,
            )
        ]
        + [
            (
                (
                    "https://www.bahn.de/service/zug/db-lounge/speisen-getraenke",
                    "Speisen Getränke Premium Lounge",
                ),
                extract_pdf_text,
            )
        ]
    )
    for (url, name), func in links:
        print("Downloading", name)
        last_mod = last_modified_data.get(name) if not args.force else None
        content, last_modified = download_if_modified(url, if_modified_since=last_mod)
        if content is None:
            print(name, "was not modified, skipping")
            continue
        print("Extracting data from", name)
        # extract_pdf_table(content, args.output / f"{name}.csv")
        func(content, args.output, name)

        last_modified_data[name] = last_modified

    with open(last_modified_csv, "w") as f:
        writer = csv.DictWriter(f, fieldnames=["name", "date"])
        writer.writeheader()
        writer.writerows({"name": k, "date": v} for k, v in last_modified_data.items())
