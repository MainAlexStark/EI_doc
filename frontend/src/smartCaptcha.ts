/**
 * Тонкая обёртка над виджетом Yandex SmartCaptcha (window.smartCaptcha).
 *
 * Скрипт грузится лениво и один раз на всё приложение — сейчас его использует
 * только публичная форма заявки (PublicRequestForm.tsx), но ничто не мешает
 * переиспользовать в другом месте, если понадобится вторая форма.
 */

export type SmartCaptchaApi = {
  render: (
    container: HTMLElement,
    options: { sitekey: string; hl?: string; callback: (token: string) => void },
  ) => number;
  reset: (widgetId: number) => void;
  destroy: (widgetId: number) => void;
};

declare global {
  interface Window {
    smartCaptcha?: SmartCaptchaApi;
  }
}

const SCRIPT_URL = "https://smartcaptcha.yandexcloud.net/captcha.js";
let loadPromise: Promise<SmartCaptchaApi> | null = null;

export function loadSmartCaptcha(): Promise<SmartCaptchaApi> {
  if (window.smartCaptcha) return Promise.resolve(window.smartCaptcha);
  if (loadPromise) return loadPromise;

  loadPromise = new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.src = SCRIPT_URL;
    script.async = true;
    script.onload = () => {
      if (window.smartCaptcha) resolve(window.smartCaptcha);
      else reject(new Error("Yandex SmartCaptcha не инициализировалась"));
    };
    script.onerror = () => reject(new Error("Не удалось загрузить капчу"));
    document.head.appendChild(script);
  });
  return loadPromise;
}
