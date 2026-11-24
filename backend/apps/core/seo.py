"""SEO сайта фирмы: мета-теги, schema.org и текст страницы прямо в HTML.

Сайт — SPA (frontend/src/landing/Landing.tsx), и без этого поисковик видел бы
один и тот же index.html на всех адресах. Здесь под каждый публичный адрес
собирается свой блок <head> (title, description, canonical, OpenGraph,
JSON-LD) и простой HTML с заголовком и текстом внутри #root — React при
загрузке заменяет его живой страницей, а робот без JS читает текст сразу.

Тексты — те же, что на странице (Landing.tsx, firm.ts): поменяли цену,
телефон или вопрос там — поправьте и здесь.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from django.conf import settings
from django.utils.html import escape

PHONE = "+7 953 697-00-83"
PHONE_LD = "+7-953-697-00-83"
ADDRESS = "г. Киров, ул. Горького, 4"
HOURS = "пн–пт, 8:00–17:00"
OFFICIAL = "Работаем официально: аттестованы в области метрологии, результат каждой поверки вносим в ФГИС «Аршин»."

SEO_START = "<!--seo-->"
SEO_END = "<!--/seo-->"
ROOT_EMPTY = '<div id="root"></div>'

FAQ_METERS = [
    ("Нужно ли снимать счётчик?", "Нет, поверяем на месте — нужна только вода из крана. Если удобнее, можете снять счётчик сами и привезти к нам в офис."),
    ("Какой документ я получу?", "Результат вносится в ФГИС «Аршин» — это и есть официальное подтверждение, управляющая компания видит его сама. Бумажное свидетельство — по запросу."),
    ("Что если счётчик не пройдёт поверку?", "Выдадим извещение о непригодности и подскажем, какой счётчик поставить взамен."),
    ("Как оплатить?", "Переводом на карту МИР или по СБП после поверки. Организациям — на расчётный счёт по счёту."),
]
FAQ_BUSINESS = [
    ("Работаете по всей России?", "Да. Поверку и аттестацию проводим с выездом на вашу площадку или в нашей лаборатории — формат согласуем под объём работ."),
    ("Какие документы получим?", "Запись о поверке в ФГИС «Аршин», по запросу — свидетельства; для аттестации — протокол и аттестат. Договор, счёт и закрывающие документы — для бухгалтерии."),
    ("Работаете с госзаказчиками?", "Да, участвуем в закупках по 44-ФЗ и 223-ФЗ."),
    ("Что будет без поверки?", "Измерения неповеренными приборами не имеют юридической силы, а за их применение предусмотрен штраф по ст. 19.19 КоАП РФ."),
    ("Только запускаем производство — с чего начать?", "Оставьте заявку и опишите, что будете делать. Подскажем, какие приборы нужно поверять, а какое оборудование — аттестовать."),
]


@dataclass(frozen=True)
class Page:
    canonical: str  # путь, на который указывает rel=canonical
    title: str
    description: str
    h1: str
    lead: str
    points: list[str] = field(default_factory=list)
    faq: list[tuple[str, str]] = field(default_factory=list)
    keywords: str = ""
    kind: str = "meters"  # meters | business | other
    city: str = ""  # для schema.org areaServed
    index: bool = True


def _meters(city: str, city_in: str, path: str) -> Page:
    return Page(
        canonical=path,
        title=f"Поверка счётчиков воды {city_in} — без снятия, от 500 ₽ | Единица Измерения",
        description=(
            f"Поверка счётчиков воды {city_in}: на дому без демонтажа или в офисе. "
            f"500 ₽ в городе, 600 ₽ в пригороде. Официально, запись в ФГИС «Аршин». Тел. {PHONE}."
        ),
        h1=f"Поверка счётчиков {city}",
        lead="Поверим счётчики воды у вас дома без снятия — или примем счётчик в офисе, если вы сняли его сами.",
        points=[
            "500 ₽ — в городе, 600 ₽ — в пригороде",
            "Выездная поверка без демонтажа, окно визита — 2 часа",
            f"Поверка с демонтажом: привезите счётчик в офис — {ADDRESS}",
            "Оплата переводом на карту МИР или по СБП, организациям — на расчётный счёт",
            OFFICIAL,
        ],
        faq=FAQ_METERS,
        keywords=f"поверка счётчиков {city}, поверка счетчиков воды {city}, поверка без снятия, поверка водосчётчиков на дому",
        kind="meters",
        city=city,
    )


PAGES: dict[str, Page] = {
    "/schetchiki/": _meters("Киров", "в Кирове", "/schetchiki/"),
    "/schetchiki/yaransk/": _meters("Яранск", "в Яранске", "/schetchiki/yaransk/"),
    "/biznes/": Page(
        canonical="/biznes/",
        title="Метрология для бизнеса: поверка СИ и аттестация оборудования | Единица Измерения",
        description=(
            "Поверка средств измерений и аттестация испытательного оборудования по всей России. "
            f"Договор, оплата на расчётный счёт, запись в ФГИС «Аршин». Тел. {PHONE}."
        ),
        h1="Метрология для вашего бизнеса",
        lead=(
            "Поверка средств измерений и аттестация испытательного оборудования для предприятий, "
            "лабораторий и торговли — по договору, с полным пакетом документов."
        ),
        points=[
            "Поверка средств измерений: манометры, весы, термометры, датчики и другие СИ",
            "Аттестация испытательного оборудования по ГОСТ Р 8.568 — протокол и аттестат",
            "Работаем по всей России: на вашей площадке или в нашей лаборатории",
            "Оплата на расчётный счёт по договору, закупки по 44-ФЗ и 223-ФЗ",
            OFFICIAL,
        ],
        faq=FAQ_BUSINESS,
        keywords="поверка средств измерений, аттестация испытательного оборудования, метрология для предприятий, поверка манометров, поверка весов",
        kind="business",
    ),
}
# старый адрес сайта — та же страница, canonical ведёт на новый
PAGES["/o-nas/"] = PAGES["/schetchiki/"]
PAGES["/zayavka/"] = Page(
    canonical="/zayavka/", title="Заявка на поверку — Единица Измерения",
    description=f"Оставьте заявку на поверку — перезвоним и согласуем время. Тел. {PHONE}.",
    h1="Заявка на поверку", lead="Минута на заполнение — остальное уточним по телефону.",
    kind="other", index=False,
)
CABINET = Page(
    canonical="/", title="EI_doc — журнал поверок", description="Рабочий кабинет сотрудников.",
    h1="", lead="", kind="other", index=False,
)

SITEMAP_PATHS = ["/schetchiki/", "/schetchiki/yaransk/", "/biznes/"]


def base_url(request) -> str:
    return (getattr(settings, "APP_PUBLIC_URL", "") or request.build_absolute_uri("/")).rstrip("/")


def page_for(path: str) -> Page:
    return PAGES.get(path, CABINET)


def _organization(base: str) -> dict:
    return {
        "@type": "LocalBusiness",
        "@id": f"{base}/#org",
        "name": "ООО «Единица Измерения»",
        "alternateName": "Единица Измерения",
        "url": f"{base}/schetchiki/",
        "telephone": PHONE_LD,
        "image": f"{base}/icon-512.png",
        "priceRange": "от 500 ₽",
        "paymentAccepted": "Банковский перевод, СБП, карта МИР, оплата на расчётный счёт",
        "address": {
            "@type": "PostalAddress",
            "streetAddress": "ул. Горького, 4",
            "addressLocality": "Киров",
            "addressRegion": "Кировская область",
            "addressCountry": "RU",
        },
        "openingHoursSpecification": [{
            "@type": "OpeningHoursSpecification",
            "dayOfWeek": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"],
            "opens": "08:00",
            "closes": "17:00",
        }],
    }


def json_ld(page: Page, base: str) -> list[dict]:
    if page.kind not in ("meters", "business"):
        return []
    org = _organization(base)
    url = base + page.canonical
    if page.kind == "meters":
        service = {
            "@type": "Service",
            "name": f"Поверка счётчиков воды — {page.city}",
            "serviceType": "Поверка счётчиков воды",
            "provider": {"@id": org["@id"]},
            "areaServed": {"@type": "City", "name": page.city},
            "url": url,
            "offers": [
                {"@type": "Offer", "name": "Поверка в городе", "price": "500", "priceCurrency": "RUB"},
                {"@type": "Offer", "name": "Поверка в пригороде", "price": "600", "priceCurrency": "RUB"},
            ],
        }
    else:
        service = {
            "@type": "Service",
            "name": "Поверка средств измерений и аттестация испытательного оборудования",
            "serviceType": "Метрологические услуги",
            "provider": {"@id": org["@id"]},
            "areaServed": {"@type": "Country", "name": "Россия"},
            "url": url,
        }
    faq = {
        "@type": "FAQPage",
        "mainEntity": [
            {"@type": "Question", "name": q, "acceptedAnswer": {"@type": "Answer", "text": a}} for q, a in page.faq
        ],
    }
    return [{"@context": "https://schema.org", "@graph": [org, service, faq]}]


def head_block(page: Page, base: str) -> str:
    url = base + page.canonical
    tags = [
        f"<title>{escape(page.title)}</title>",
        f'<meta name="description" content="{escape(page.description)}" />',
    ]
    if not page.index:
        tags.append('<meta name="robots" content="noindex, follow" />')
        return "\n    ".join(tags)
    if page.keywords:
        tags.append(f'<meta name="keywords" content="{escape(page.keywords)}" />')
    tags += [
        f'<link rel="canonical" href="{escape(url)}" />',
        '<meta property="og:type" content="website" />',
        '<meta property="og:locale" content="ru_RU" />',
        '<meta property="og:site_name" content="Единица Измерения" />',
        f'<meta property="og:url" content="{escape(url)}" />',
        f'<meta property="og:title" content="{escape(page.title)}" />',
        f'<meta property="og:description" content="{escape(page.description)}" />',
        f'<meta property="og:image" content="{escape(base)}/icon-512.png" />',
        '<meta name="twitter:card" content="summary" />',
        '<meta name="geo.region" content="RU-KIR" />',
    ]
    if page.city:
        tags.append(f'<meta name="geo.placename" content="{escape(page.city)}" />')
    for block in json_ld(page, base):
        # "</" внутри JSON закрыл бы тег script — экранируем
        data = json.dumps(block, ensure_ascii=False).replace("</", "<\\/")
        tags.append(f'<script type="application/ld+json">{data}</script>')
    return "\n    ".join(tags)


def prerender(page: Page) -> str:
    """Текст страницы для робота без JS. Живая страница заменяет его при загрузке."""
    if not page.index or not page.h1:
        return ""
    tel = "tel:" + "".join(ch for ch in PHONE if ch.isdigit() or ch == "+")
    parts = [
        "<main>",
        f"<h1>{escape(page.h1)}</h1>",
        f"<p>{escape(page.lead)}</p>",
        "<ul>" + "".join(f"<li>{escape(p)}</li>" for p in page.points) + "</ul>",
    ]
    if page.faq:
        parts.append("<h2>Частые вопросы</h2>")
        parts += [f"<h3>{escape(q)}</h3><p>{escape(a)}</p>" for q, a in page.faq]
    other = ('<a href="/biznes/">Метрология для бизнеса</a>' if page.kind == "meters"
             else '<a href="/schetchiki/">Поверка счётчиков в Кирове</a> · <a href="/schetchiki/yaransk/">в Яранске</a>')
    parts += [
        "<h2>Контакты</h2>",
        f'<p>Телефон: <a href="{tel}">{escape(PHONE)}</a>. Офис: {escape(ADDRESS)}, {escape(HOURS)}.</p>',
        f"<p>{other}</p>",
        "</main>",
    ]
    return "".join(parts)


def render_index(html: str, path: str, base: str) -> str:
    """index.html с блоком SEO под адрес. Без маркеров в шаблоне — как есть."""
    page = page_for(path)
    start, end = html.find(SEO_START), html.find(SEO_END)
    if start != -1 and end > start:
        html = html[: start + len(SEO_START)] + "\n    " + head_block(page, base) + "\n    " + html[end:]
    body = prerender(page)
    if body:
        html = html.replace(ROOT_EMPTY, f'<div id="root">{body}</div>', 1)
    return html


def robots_txt(base: str) -> str:
    return "\n".join([
        "User-agent: *",
        "Allow: /schetchiki/",
        "Allow: /biznes/",
        "Disallow: /$",
        "Disallow: /api/",
        "Disallow: /admin/",
        "Disallow: /zayavka/",
        "Disallow: /o-nas/",
        "",
        f"Sitemap: {base}/sitemap.xml",
        "",
    ])


def sitemap_xml(base: str) -> str:
    urls = "".join(
        f"<url><loc>{escape(base + path)}</loc><changefreq>monthly</changefreq>"
        f"<priority>{'1.0' if path == '/schetchiki/' else '0.8'}</priority></url>"
        for path in SITEMAP_PATHS
    )
    return ('<?xml version="1.0" encoding="UTF-8"?>'
            f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>')
