"""Install the MIT-licensed ECDICT English-Chinese dictionary locally."""
import csv
from contextlib import closing
import sqlite3
import tempfile
from pathlib import Path
from urllib.request import urlopen
import shutil


def prepare(folder):
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / "ecdict.sqlite3"
    if target.exists():
        print("ECDICT 本地英汉词典已准备好。", flush=True)
        return
    base = "https://raw.githubusercontent.com/skywind3000/ECDICT/master/"
    with tempfile.TemporaryDirectory(dir=folder) as temporary:
        temp = Path(temporary)
        print("正在下载 ECDICT 英汉词库（约 66 MB）…", flush=True)
        for name in ("ecdict.csv", "LICENSE"):
            with urlopen(base + name, timeout=90) as response, (temp / name).open("wb") as output:
                shutil.copyfileobj(response, output)
        with closing(sqlite3.connect(temp / "ecdict.sqlite3")) as db, db:
            db.execute("CREATE TABLE entries (word TEXT PRIMARY KEY COLLATE NOCASE, phonetic TEXT, definition TEXT, translation TEXT, pos TEXT, exchange TEXT)")
            with (temp / "ecdict.csv").open(encoding="utf-8-sig", newline="") as source:
                rows = csv.DictReader(source)
                db.executemany("INSERT OR REPLACE INTO entries VALUES (?,?,?,?,?,?)", (
                    tuple(row.get(key, "") for key in ("word", "phonetic", "definition", "translation", "pos", "exchange")) for row in rows if row.get("word")
                ))
            count = db.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
            if count < 100000:
                raise RuntimeError("词库下载不完整，请重试。")
        shutil.copyfile(temp / "LICENSE", folder / "ECDICT-LICENSE.txt")
        (temp / "ecdict.sqlite3").replace(target)
    print(f"ECDICT 本地英汉词典已准备好，共 {count:,} 个词条。", flush=True)


if __name__ == "__main__":
    prepare(Path(__file__).resolve().parent / "data" / "dictionaries")
