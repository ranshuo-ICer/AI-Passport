#!/usr/bin/env python3
"""把 pwa/ 发布到 GitHub Pages（gh-pages 分支）。

为什么用 gh-pages 分支 + git 底层命令，而不是 GitHub Actions：

* Actions 那条路要把仓库设置的 Pages Source 改成「GitHub Actions」；
  `actions/configure-pages` 虽然有个 `enablement` 能自动启用 Pages，但它的文档
  写明**必须提供非 GITHUB_TOKEN 的 PAT**（需要 repo scope），默认令牌不行。
  也就是说 Actions 路线无论怎样都要人点一次设置。
* 而 `gh-pages` 分支有机会被 GitHub 自动启用（历史行为），不用点任何东西；
  要手点时，也是同一个下拉框、同样一次。所以这条路的期望成本更低。

实现细节：用 `git hash-object` / `update-index` / `write-tree` / `commit-tree`
直接构造一个**孤立**提交，全程不碰 main 的工作区，也不切换分支 ——
`git worktree` 和 `checkout --orphan` 都会动到当前检出的状态，这个项目里
那样做风险太大。

    python tools/pages_deploy.py            # 构造并推送
    python tools/pages_deploy.py --dry-run  # 只打印将要提交的文件
"""

import argparse
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PWA = os.path.join(ROOT, "pwa")

BRANCH = "gh-pages"
# 发布时要排除的东西：图标生成脚本是开发用的，站点上不需要
EXCLUDE_EXT = (".py",)
# Jekyll 会吃掉下划线开头的文件、还会多一层处理；静态站点直接关掉它
NOJEKYLL = ".nojekyll"


def git(*args, **kw):
    return subprocess.run(["git"] + list(args), cwd=ROOT, capture_output=True,
                          text=True, encoding="utf-8", errors="replace", **kw)


def collect():
    """要发布到站点根目录的文件：{仓库内相对路径: 站点内路径}。"""
    out = {}
    for name in sorted(os.listdir(PWA)):
        path = os.path.join(PWA, name)
        if not os.path.isfile(path):
            continue
        if os.path.splitext(name)[1].lower() in EXCLUDE_EXT:
            continue
        out[name] = path
    return out


def build_commit(files):
    """用临时 index 造一棵树 + 一个孤立提交，返回提交哈希。"""
    index = os.path.join(ROOT, ".git", "pages-index.tmp")
    if os.path.exists(index):
        os.remove(index)
    env = dict(os.environ, GIT_INDEX_FILE=index)

    def g(*args, **kw):
        return subprocess.run(["git"] + list(args), cwd=ROOT, env=env,
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace", **kw)

    g("read-tree", "--empty")
    # .nojekyll 是空的
    empty = git("hash-object", "-w", "--stdin", input="").stdout.strip()
    g("update-index", "--add", "--cacheinfo", "100644,%s,%s" % (empty, NOJEKYLL))

    for site_name, src in files.items():
        blob = git("hash-object", "-w", src).stdout.strip()
        g("update-index", "--add", "--cacheinfo",
          "100644,%s,%s" % (blob, site_name))
    tree = g("write-tree").stdout.strip()
    if not tree:
        raise SystemExit("write-tree 失败")
    msg = "deploy(pages): publish pwa/ to %s\n\n由 tools/pages_deploy.py 生成，"\
          "不要手工编辑这个分支 —— 内容来自 main 的 pwa/。" % BRANCH
    commit = git("commit-tree", tree, "-m", msg).stdout.strip()
    if not commit:
        raise SystemExit("commit-tree 失败")
    os.remove(index)
    return commit


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--remote", default="origin")
    args = ap.parse_args()

    files = collect()
    if not files:
        raise SystemExit("pwa/ 里没有可发布的文件")

    if args.dry_run:
        print("将发布 %d 个文件到站点根目录：" % len(files))
        for n in sorted(files):
            print("  %-24s %7d B" % (n, os.path.getsize(files[n])))
        print("  %-24s %7d B" % (NOJEKYLL, 0))
        return 0

    commit = build_commit(files)
    print("已构造孤立提交 %s（%d 个文件）" % (commit[:12], len(files)))

    r = git("update-ref", "refs/heads/%s" % BRANCH, commit)
    if r.returncode != 0:
        raise SystemExit("update-ref 失败：%s" % (r.stderr or r.stdout))

    r = git("push", "--force", args.remote, "%s:%s" % (BRANCH, BRANCH))
    out = (r.stdout or "") + (r.stderr or "")
    for ln in out.splitlines():
        if ln.strip():
            print("  " + ln.strip())
    if r.returncode != 0:
        raise SystemExit("推送失败（Clash 代理开着吗？）")
    print("\n推好了。站点地址：https://<你的用户名>.github.io/<仓库名>/")
    print("如果打开是 404，去仓库 Settings → Pages → Source 选")
    print("  Deploy from a branch → %s → / (root) → Save" % BRANCH)
    return 0


if __name__ == "__main__":
    sys.exit(main())
