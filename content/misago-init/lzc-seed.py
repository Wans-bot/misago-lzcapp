#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Misago 演示内容与站点设置初始化（幂等，可重复执行）。

由 manifest 的 setup_script 在每次启动时调用（migrate/createsuperuser 之后）：
  1. 站点地址 forum_address：取平台注入的 LAZYCAT_APP_DOMAIN，
     让管理后台的「论坛地址」系统检查通过（邮件链接使用公开域名）。
  2. 演示内容：把默认的 First category 更名为「综合讨论」，补充
     公告中心 / 使用帮助 / 建议反馈 版块与若干中文明主题、回复，
     让新安装在商店截图与首次使用中都有完整可看的论坛形态。

仅在首次初始化时完整执行一次（标记文件写在持久化的 userdata 目录）；
重复执行时按存在性判断补齐，不会产生重复内容或覆盖用户改动。
"""

import html as html_mod
import os
import sys
from datetime import timedelta

sys.path.insert(0, "/misago")
os.chdir("/misago")

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "misagodocker.settings")
django.setup()

from django.utils import timezone

from misago.acl.cache import clear_acl_cache
from misago.categories.models import Category, RoleCategoryACL
from misago.conf.cache import clear_settings_cache
from misago.conf.models import Setting
from misago.threads.checksums import update_post_checksum
from misago.threads.models import Post, Thread
from misago.users.models import User

MARKER_PATH = "/misago/userdata/.lzc-demo-seeded"


def log(*args):
    print("[misago-seed]", *args, flush=True)


def paragraphs_html(text):
    """把纯文本段落转成简单的 HTML（<p> 段落、单换行转 <br>）。"""
    out = []
    for block in text.strip().split("\n\n"):
        block = block.strip()
        if block:
            out.append("<p>%s</p>" % html_mod.escape(block).replace("\n", "<br>"))
    return "".join(out)


def ensure_forum_address():
    domain = (os.environ.get("LAZYCAT_APP_DOMAIN") or "").strip()
    if not domain:
        log("LAZYCAT_APP_DOMAIN 未设置，跳过 forum_address")
        return
    # 注意：misago 管理后台的系统检查用 build_absolute_uri("/") 精确比对，
    # 必须带结尾斜杠（实测 "https://host" 会显示「地址似乎不正确」）。
    address = "https://%s/" % domain
    current = (
        Setting.objects.filter(setting="forum_address")
        .values_list("dry_value", flat=True)
        .first()
    )
    if current == address:
        log("forum_address 已是", address)
    else:
        updated = Setting.objects.change_setting("forum_address", dry_value=address)
        log("forum_address ->", address, "(rows updated: %s)" % updated)
    # 关键：设置值由 ORM 直接更新，misago 只在管理页表单保存路径清
    # settings 缓存版本；而启动早期（迁移/post_migrate 阶段）可能已按旧值
    # 构建过 Redis 快照。显式失效版本，保证管理后台「论坛地址」检查读到新值
    # （实测首装若不清理，检查会显示「论坛地址未配置」且长期不自愈）。
    clear_settings_cache()
    log("settings 缓存版本已失效")


def ensure_category(root, acl_source, slug, name, description):
    existing = Category.objects.filter(slug=slug).first()
    if existing:
        return existing, False
    category = Category()
    category.set_name(name)
    category.description = description
    category.insert_at(root, position="last-child", save=True)
    acls = []
    for acl in acl_source.category_role_set.all():
        acls.append(
            RoleCategoryACL(
                role_id=acl.role_id,
                category=category,
                category_role_id=acl.category_role_id,
            )
        )
    if acls:
        RoleCategoryACL.objects.bulk_create(acls)
    log("版块已创建:", name, "/", category.slug)
    return category, True


def add_post(thread, author, text, posted_on):
    post = Post.objects.create(
        category=thread.category,
        thread=thread,
        poster=author,
        poster_name=author.username,
        original=text,
        parsed=paragraphs_html(text),
        checksum="pending",
        posted_on=posted_on,
        updated_on=posted_on,
    )
    update_post_checksum(post)
    post.save(update_fields=["checksum"])
    return post


def ensure_thread(category, author, slug, title, first_text, replies, started_on, pinned=False):
    if Thread.objects.filter(slug=slug).exists():
        log("主题已存在:", slug)
        return
    kwargs = {
        "category": category,
        "title": title,
        "slug": slug,
        "started_on": started_on,
        "last_post_on": started_on,
        "starter": author,
        "starter_name": author.username,
        "starter_slug": author.slug,
        "last_poster": author,
        "last_poster_name": author.username,
        "last_poster_slug": author.slug,
    }
    if pinned:
        kwargs["weight"] = 1
    thread = Thread.objects.create(**kwargs)
    add_post(thread, author, first_text, started_on)
    last_on = started_on
    for offset, text in replies:
        last_on = started_on + offset
        add_post(thread, author, text, last_on)
    thread.last_post_on = last_on
    thread.synchronize()
    thread.save()
    category.synchronize()
    category.save()
    log("主题已创建:", title)


def main():
    if os.path.exists(MARKER_PATH):
        log("已初始化过（%s 存在），跳过" % MARKER_PATH)
        return

    username = os.environ.get("MISAGO_ADMIN_USER", "admin")
    author = User.objects.filter(username=username).first()
    if author is None:
        log("管理员 %s 尚未创建，跳过演示内容" % username)
        return

    ensure_forum_address()

    root = Category.objects.get(slug="root")

    # 「综合讨论」：把安装向导创建的默认版块改名为中文
    first = Category.objects.filter(slug="first-category").first()
    if first is not None:
        first.set_name("综合讨论")
        first.description = "畅所欲言，分享你的话题与日常讨论"
        first.save()
        log("默认版块已更名为: 综合讨论 /", first.slug)
        general = first
    else:
        general = Category.objects.filter(slug="zong-he-tao-lun").first()

    acl_source = general or Category.objects.get(slug="root")

    announcements, _ = ensure_category(
        root, acl_source, "gong-gao-zhong-xin", "公告中心", "站点公告与重要通知"
    )
    help_board, _ = ensure_category(
        root, acl_source, "shi-yong-bang-zhu", "使用帮助", "使用技巧、常见问题解答"
    )
    feedback, _ = ensure_category(
        root, acl_source, "jian-yi-fan-kui", "建议反馈", "功能建议与问题反馈"
    )

    now = timezone.now()
    day = timedelta(days=1)

    ensure_thread(
        announcements,
        author,
        "huan-ying-lai-dao-ben-zhan-lun-tan",
        "欢迎来到本站论坛",
        "欢迎来到本站论坛！这是一个内置的示例主题，用来演示论坛的版块、主题与回复功能。\n\n"
        "你可以把它当作起点：在「综合讨论」里聊天，在「使用帮助」里找答案，"
        "也可以随时在管理后台里删除本主题、创建自己的版块与用户组。\n\n"
        "祝讨论愉快！",
        [
            (
                timedelta(minutes=25),
                "想了解论坛的完整能力，可以看看「使用帮助」版块：支持 Markdown 排版、"
                "@提及、站内私信、通知中心与全文搜索。",
            )
        ],
        started_on=now - 2 * day,
        pinned=True,
    )

    ensure_thread(
        announcements,
        author,
        "fa-tie-yu-hui-fu-kuai-su-zhi-nan",
        "发帖与回复快速指南",
        "三步发布你的第一篇主题：\n\n"
        "1. 进入一个版块，例如「综合讨论」；\n"
        "2. 点击「新主题」按钮；\n"
        "3. 填写标题与正文（支持 Markdown），然后发布。\n\n"
        "回复主题时，在帖子下方使用编辑器输入内容即可。",
        [
            (
                timedelta(hours=1),
                "小提示：编辑器支持代码块、引用、列表与图片上传，善用它们让帖子更清晰。",
            )
        ],
        started_on=now - day,
    )

    ensure_thread(
        general,
        author,
        "da-jia-dou-zai-wei-fu-shang-pao-le-na-xie-fu-wu",
        "大家都在微服上跑了哪些服务？",
        "论坛跑起来了，顺便开个话题：大家在微服上部署了哪些好玩或实用的服务？\n\n"
        "欢迎晒一晒你的「应用清单」，也可以聊聊各自的使用体验与踩坑经历。",
        [
            (
                timedelta(minutes=40),
                "先抛砖引玉：论坛 + 网盘 + 媒体库是我的三件套，日常使用很省心。",
            ),
            (
                timedelta(hours=2),
                "我把笔记、RSS 和监控也都搬上来了，最大的好处是数据都在自己手里。",
            ),
        ],
        started_on=now - timedelta(hours=8),
    )

    ensure_thread(
        general,
        author,
        "zhe-teng-xiao-ji-ru-he-zheng-li-ni-de-zi-tuo-guan-fu-wu-qing-dan",
        "折腾小记：如何整理你的自托管服务清单",
        "服务越来越多之后，一份清单就很有必要了。\n\n"
        "我习惯按「核心 / 常用 / 尝鲜」三档整理，并记录每个服务的备份方式与更新节奏。\n\n"
        "你们是怎么管理自托管服务的？欢迎在回复里聊聊。",
        [],
        started_on=now - timedelta(hours=5),
    )

    ensure_thread(
        help_board,
        author,
        "ru-he-xiu-gai-ni-cheng-tou-xiang-yu-ge-ren-qian-ming",
        "如何修改昵称、头像与个人签名？",
        "点击右上角头像进入「用户设置」，在弹窗里可以：\n\n"
        "- 修改昵称与个人签名；\n"
        "- 上传或更换头像；\n"
        "- 切换界面语言与时区；\n"
        "- 配置通知与隐私选项。\n\n"
        "保存后立即生效，其他用户会看到你的新资料。",
        [
            (
                timedelta(minutes=30),
                "头像也支持从懒猫网盘选择图片，上传前会自动裁剪为正方形。",
            )
        ],
        started_on=now - day,
    )

    ensure_thread(
        feedback,
        author,
        "huan-ying-zai-zhe-li-ti-jiao-jian-yi-yu-wen-ti-fan-kui",
        "欢迎在这里提交建议与问题反馈",
        "使用中遇到问题，或对论坛功能有想法？直接在本版块发帖即可。\n\n"
        "为了让问题更好定位，反馈时建议说明：操作步骤、预期结果与实际结果；"
        "如果涉及页面显示问题，附上截图会更高效。",
        [
            (
                timedelta(hours=1),
                "已收到大家的建议，我们会持续跟进，并在主题回复中同步进展。",
            )
        ],
        started_on=now - timedelta(hours=3),
    )

    # 同步演示作者的统计计数：ORM 播种不经过发帖信号，User.posts/threads/
    # last_posted_on 不会自动更新（实测帖子侧栏显示「0 篇帖子」）。这里按
    # 可见帖子/主题重算，幂等。
    visible_posts = Post.objects.filter(
        poster=author, is_unapproved=False, is_hidden=False
    ).order_by("posted_on")
    author.posts = visible_posts.count()
    author.threads = Thread.objects.filter(
        starter=author, is_unapproved=False, is_hidden=False
    ).count()
    last_post = visible_posts.last()
    if last_post is not None:
        author.last_posted_on = last_post.posted_on
    author.save(update_fields=["posts", "threads", "last_posted_on"])
    log("作者统计已同步: posts=%s threads=%s" % (author.posts, author.threads))

    # 新建版块与权限行绕过 ORM 信号，需显式失效 ACL 缓存版本，
    # 否则匿名/已登录用户的可见性缓存（Redis）不会包含新内容。
    clear_acl_cache()
    log("ACL 缓存版本已失效，新内容立即生效")

    with open(MARKER_PATH, "w", encoding="utf-8") as fh:
        fh.write("seeded %s\n" % timezone.now().isoformat())
    log("演示内容初始化完成")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # 失败不阻塞应用启动
        log("初始化失败:", repr(exc))
        sys.exit(0)
