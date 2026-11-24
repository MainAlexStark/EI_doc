/** Реквизиты фирмы для публичных страниц (/schetchiki/, /biznes/, /zayavka/).
 *  Поменяли телефон, адрес или часы — поправьте и backend/apps/core/seo.py
 *  (title, description, schema.org для поисковиков). */
export const FIRM = {
  name: "ООО «Единица Измерения»",
  short: "Единица Измерения",
  phone: "+7 953 697-00-83",
  email: "",
  address: "г. Киров, ул. Горького, 4",
  hours: "пн–пт, 8:00–17:00",
  visits: "в любой день недели",
  mapUrl: "https://yandex.ru/maps/?text=" + encodeURIComponent("Киров, улица Горького, 4"),
} as const;

export const telHref = (phone: string = FIRM.phone) => `tel:${phone.replace(/[^\d+]/g, "")}`;
