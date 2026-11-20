#!/usr/bin/env python3
"""展开 I3/7 各国文学。

中图法 I3/7 原注：「依世界地区表分，再依下表分。例：日本近代小说集的号码是
I313.44。」本脚本按该规则把单个类号 I3/7 展开成 洲 → 地区 → 国家 → 复分子目。

两份输入均为人工核对过的数据文件，不使用类号语法推断层级：
  world_regions.tsv  世界地区表 3/4/5/6/7 段，每行 <父类号> <类号> <类名>
  subdivision.tsv    I3/7 专类复分表，每行 <父复分号> <复分号> <类名>，
                     父复分号为空表示该号直接挂在国家号下

不使用类号语法推断层级的原因：复分号与该国下位地区号在类号形式上无法区分。
例如 I312.6 既是「韩国」这个地区号，也可能是「散文、杂著」复分号；靠字符串前缀
判断会把两者混为一谈，这也正是 I312 类号冲突的来源。父级因此写成显式数据。

用法：
    python3 scripts/build_i37.py            # 就地展开 clc.json 并重写 clc.md
    python3 scripts/build_i37.py --check    # 只校验，不写文件
"""
import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CLC_JSON = REPO / "clc.json"
CLC_MD = REPO / "clc.md"
WORLD_REGIONS = REPO / "scripts" / "world_regions.tsv"
SUBDIVISION = REPO / "scripts" / "subdivision.tsv"

ROOT_ID = "I3/7"


def load_tsv(path):
    """读取三列 TSV，返回 (父级, 类号, 类名) 列表，保持文件内顺序。"""
    rows = []
    with path.open(encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            line = line.rstrip("\n")
            if not line:
                continue
            parts = line.split("\t")
            if len(parts) != 3:
                raise SystemExit(f"{path}:{lineno} 需要 3 列，实际 {len(parts)} 列")
            rows.append(tuple(parts))
    return rows


def find(nodes, cid):
    for node in nodes:
        if node["id"] == cid:
            return node
        found = find(node.get("children") or [], cid)
        if found is not None:
            return found
    return None


def walk(nodes):
    for node in nodes:
        yield node
        yield from walk(node.get("children") or [])


def country_prefix(country_id):
    """国家号接复分号时的前缀。

    三位国家号用小数点接复分号（I313 + 45 → I313.45）；已带小数点的号直接续接
    （I312.6 + 45 → I312.645）。
    """
    return country_id + "." if "." not in country_id else country_id


def build_subtree(region_rows, sub_rows):
    """按两份表构造 I3/7 的下级子树，返回洲级节点列表。"""
    children_of_region = {ROOT_ID: []}
    for parent, cid, desc in region_rows:
        children_of_region.setdefault(cid, [])
        children_of_region.setdefault(parent, []).append(cid)

    # 复分子目按父复分号分组，空串表示直接挂在国家号下
    sub_by_parent = {}
    for parent, code, desc in sub_rows:
        sub_by_parent.setdefault(parent, []).append((code, desc))

    def make_sub_nodes(prefix, parent_code):
        nodes = []
        for code, desc in sub_by_parent.get(parent_code, []):
            node = {"id": prefix + code, "desc": desc}
            grandchildren = make_sub_nodes(prefix, code)
            if grandchildren:
                node["children"] = grandchildren
            nodes.append(node)
        return nodes

    def make_region_node(cid, desc):
        node = {"id": cid, "desc": desc}
        has_subregions = bool(children_of_region.get(cid))
        if has_subregions:
            # 有下位地区号的类只作地区容器，不挂复分子目，否则复分号会与地区号撞号
            node["children"] = [
                make_region_node(child, region_desc[child])
                for child in children_of_region[cid]
            ]
        elif not cid.startswith("["):
            # 交替类目（方括号）保留类号但不展开
            subs = make_sub_nodes(country_prefix(cid), "")
            if subs:
                node["children"] = subs
        return node

    region_desc = {cid: desc for _, cid, desc in region_rows}
    return [
        make_region_node(child, region_desc[child])
        for child in children_of_region[ROOT_ID]
    ]


def write_md(data, path):
    """按 clc.md 既有格式输出：每级 2 空格缩进，文末无换行。"""
    lines = []

    def emit(nodes, depth):
        for node in nodes:
            lines.append("  " * depth + "* " + node["id"] + " " + node["desc"])
            emit(node.get("children") or [], depth + 1)

    emit(data, 0)
    path.write_text("\n".join(lines), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="只校验，不写文件")
    args = parser.parse_args()

    data = json.loads(CLC_JSON.read_text(encoding="utf-8"))
    root = find(data, ROOT_ID)
    if root is None:
        raise SystemExit(f"clc.json 中找不到 {ROOT_ID}")
    if root.get("children"):
        raise SystemExit(f"{ROOT_ID} 已有下级类目，脚本不能重复展开")

    region_rows = load_tsv(WORLD_REGIONS)
    sub_rows = load_tsv(SUBDIVISION)

    rows_by_id = {cid: (parent, desc) for parent, cid, desc in region_rows}
    if len(rows_by_id) != len(region_rows):
        raise SystemExit("world_regions.tsv 存在重复类号")
    dangling = [
        cid for parent, cid, _ in region_rows
        if parent != ROOT_ID and parent not in rows_by_id
    ]
    if dangling:
        raise SystemExit(f"world_regions.tsv 父级悬空: {dangling}")

    before = [node["id"] for node in walk(data)]
    root["children"] = build_subtree(region_rows, sub_rows)

    after = [node["id"] for node in walk(data)]
    clashes = sorted({cid for cid in before if before.count(cid) > 1})
    if clashes:
        raise SystemExit(f"展开后出现重复类号: {clashes[:10]}")
    if len(after) != len(set(after)):
        raise SystemExit("展开后类号不唯一")

    added = len(after) - len(before)
    print(f"{ROOT_ID} 下级新增 {added} 条，总类目 {len(before)} → {len(after)}")

    if args.check:
        return

    CLC_JSON.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    write_md(data, CLC_MD)
    print(f"已写入 {CLC_JSON.relative_to(REPO)} 与 {CLC_MD.relative_to(REPO)}")


if __name__ == "__main__":
    main()
