# -*- coding: utf-8 -*-
"""open_leisure.py - 从书签文件夹随机打开一个视频
用法:
  python open_leisure.py                          # 默认：Chrome / 休闲片单
  python open_leisure.py --browser edge           # 用 Edge
  python open_leisure.py --folder 我的片单        # 自定义文件夹名
  python open_leisure.py --dry-run                # 只输出不打开
"""
import json, os, random, sys, argparse, shutil, tempfile


def load_bookmarks(path):
    """读取书签 JSON，自动处理浏览器文件锁定"""
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (PermissionError, OSError):
        tmp = tempfile.NamedTemporaryFile(suffix='.json', delete=False)
        tmp.close()
        shutil.copy2(path, tmp.name)
        try:
            with open(tmp.name, 'r', encoding='utf-8') as f:
                return json.load(f)
        finally:
            os.unlink(tmp.name)


def find_folder(node, folder_name):
    """递归查找指定名称的文件夹节点"""
    if node.get('type') == 'folder':
        if node.get('name') == folder_name:
            return node
        for child in node.get('children', []):
            result = find_folder(child, folder_name)
            if result:
                return result
    return None


def collect_urls(node):
    """收集节点下所有 URL 书签（递归）"""
    urls = []
    if node.get('type') == 'url':
        name = node.get('name', '')
        url = node.get('url', '')
        if url.startswith(('http://', 'https://')):
            urls.append({'name': name, 'url': url})
    elif node.get('type') == 'folder':
        for child in node.get('children', []):
            urls.extend(collect_urls(child))
    return urls


def main():
    parser = argparse.ArgumentParser(description='从书签文件夹随机打开一个视频')
    parser.add_argument('--folder', default='休闲片单',
                        help='书签文件夹名（默认：休闲片单）')
    parser.add_argument('--browser', default='chrome', choices=['chrome', 'edge'],
                        help='浏览器（默认：chrome）')
    parser.add_argument('--dry-run', action='store_true',
                        help='只输出选中的 URL，不打开')
    args = parser.parse_args()

    # 定位书签文件
    if args.browser == 'chrome':
        bm_path = os.path.expandvars(
            r'%LOCALAPPDATA%\Google\Chrome\User Data\Default\Bookmarks')
    else:
        bm_path = os.path.expandvars(
            r'%LOCALAPPDATA%\Microsoft\Edge\User Data\Default\Bookmarks')

    if not os.path.exists(bm_path):
        print(f"ERROR: 书签文件不存在: {bm_path}")
        sys.exit(1)

    # 读取书签
    try:
        data = load_bookmarks(bm_path)
    except Exception as e:
        print(f"ERROR: 读取书签失败: {e}")
        sys.exit(1)

    # 在所有根节点下查找目标文件夹
    roots = data.get('roots', {})
    target_folder = None
    for root_key in ['bookmark_bar', 'other', 'synced']:
        root = roots.get(root_key, {})
        target_folder = find_folder(root, args.folder)
        if target_folder:
            break

    if not target_folder:
        print(f"ERROR: 找不到书签文件夹「{args.folder}」")
        print(f"请在 {args.browser} 中创建名为「{args.folder}」的书签文件夹，"
              f"把爱看的视频链接放进去")
        sys.exit(2)

    # 收集文件夹下所有书签
    urls = collect_urls(target_folder)
    if not urls:
        print(f"ERROR: 文件夹「{args.folder}」里没有有效书签")
        sys.exit(3)

    # 随机选一条并打开
    pick = random.choice(urls)
    print(f"SELECTED: {pick['name']}")
    print(f"URL: {pick['url']}")
    print(f"FROM: {args.browser} / {args.folder} ({len(urls)} bookmarks)")

    if not args.dry_run:
        os.startfile(pick['url'])


if __name__ == '__main__':
    main()
