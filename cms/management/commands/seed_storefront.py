"""Seed editable starter navigation + homepage sections (idempotent).

Only fills a menu that has no items and the homepage when it has no sections, so it
never overwrites what an administrator configured. Everything seeded is editable.
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from cms import navigation
from cms.models import HomepageSection, NavigationItem, NavigationMenu

HEADER = [
    ("همه محصولات", "/shop", []),
    ("دسته‌بندی‌ها", "", "categories"),
    ("تازه‌ها", "/shop?sort=newest", []),
    ("مجله", "/blog", []),
    ("درباره ما", "/about", []),
    ("تماس با ما", "/contact", []),
]
FOOTER = [
    ("فروشگاه", [("همه محصولات", "/shop"), ("تازه‌ها", "/shop?sort=newest"), ("پیشنهاد ما", "/shop?sort=featured")]),
    ("خدمات مشتریان", [("پیگیری سفارش", "/track-order"), ("سفارش‌های من", "/account/orders"), ("تماس با ما", "/contact")]),
    ("درباره ما", [("داستان ما", "/about"), ("مجله", "/blog")]),
]
HOMEPAGE = [
    dict(kind="hero", eyebrow="مجموعه گزیده", title="رایحه‌ای که پیش از شما می‌رسد",
         subtitle="عطرهای اصل و گزیده؛ با راهنمای نت‌ها، ماندگاری و پخش بو تا انتخابی مطمئن داشته باشید.",
         cta_text="خرید عطر", cta_url="/shop", secondary_cta_text="تازه‌های فصل", secondary_cta_url="/shop?sort=newest"),
    dict(kind="categories", eyebrow="دسته‌بندی‌ها", title="از کجا شروع کنیم؟", limit=6),
    dict(kind="new_arrivals", eyebrow="تازه رسیده", title="تازه‌های فصل",
         subtitle="جدیدترین محصولاتی که به ویترین اضافه شده‌اند.", cta_text="مشاهده همه", cta_url="/shop?sort=newest", limit=8),
    dict(kind="promo_banners", title="بنرهای تبلیغاتی", banner_position="home"),
    dict(kind="featured_products", eyebrow="انتخاب ما", title="پیشنهاد ما", cta_text="مشاهده همه",
         cta_url="/shop?sort=featured", limit=4),
    dict(kind="brands", title="برندهای منتخب", limit=12),
    dict(kind="journal", eyebrow="مجله", title="بیشتر بدانید، بهتر انتخاب کنید", cta_text="همه مقالات", cta_url="/blog", limit=3),
    dict(kind="promises", title="خدمات ما", items=[
        {"title": "تضمین اصالت", "text": "همه کالاها اصل"},
        {"title": "ارسال سریع", "text": "ارسال به سراسر کشور"},
        {"title": "مشاوره خرید", "text": "پاسخ‌گویی در ساعات کاری"},
    ]),
    dict(kind="track_order", title="پیگیری سفارش بدون ورود",
         subtitle="با شماره سفارش و موبایل، وضعیت پرداخت و ارسال را ببینید.", cta_text="پیگیری سفارش", cta_url="/track-order"),
]


class Command(BaseCommand):
    help = "Seed starter header/footer navigation and homepage sections if empty (idempotent)."

    @transaction.atomic
    def handle(self, *args, **options):
        header, _ = NavigationMenu.objects.get_or_create(location="header", defaults={"name": "منوی اصلی (هدر)"})
        footer, _ = NavigationMenu.objects.get_or_create(location="footer", defaults={"name": "منوی فوتر"})
        if not header.items.exists():
            for i, (label, url, auto) in enumerate(HEADER):
                NavigationItem.objects.create(
                    menu=header, label=label, sort_order=i,
                    link_type="internal" if url else "none", url=url,
                    auto_children=auto if isinstance(auto, str) else "",
                )
            navigation.publish(header, None)
            self.stdout.write("Header navigation seeded and published.")
        if not footer.items.exists():
            for i, (title, links) in enumerate(FOOTER):
                col = NavigationItem.objects.create(menu=footer, label=title, link_type="none", sort_order=i)
                for j, (label, url) in enumerate(links):
                    NavigationItem.objects.create(menu=footer, parent=col, label=label, url=url, sort_order=j)
            navigation.publish(footer, None)
            self.stdout.write("Footer navigation seeded and published.")
        if not HomepageSection.objects.exists():
            for i, data in enumerate(HOMEPAGE):
                HomepageSection.objects.create(sort_order=i, **data)
            self.stdout.write("Homepage sections seeded.")
        self.stdout.write(self.style.SUCCESS("Storefront content ensured."))
