/** Реквизиты фирмы для публичных страниц (/o-nas/, /zayavka/) и schema.org в index.html.
 *  Поменяли телефон или часы — поправьте и JSON-LD в frontend/index.html. */
export const FIRM = {
  name: "ООО «Единица Измерения»",
  short: "Единица Измерения",
  phone: "+7 953 137-00-83",
  email: "",
  address: "г. Киров, ул. Горького, 4",
  hours: "пн–пт, 8:00–17:00",
  visits: "в любой день недели",
  mapUrl: "https://yandex.ru/maps/?text=" + encodeURIComponent("Киров, улица Горького, 4"),
} as const;

export const telHref = (phone: string = FIRM.phone) => `tel:${phone.replace(/[^\d+]/g, "")}`;
