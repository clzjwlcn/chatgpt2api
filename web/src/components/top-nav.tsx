"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { Megaphone, Menu, X } from "lucide-react";
import { usePathname, useRouter } from "next/navigation";

import { HeaderActions } from "@/components/header-actions";
import { Button } from "@/components/ui/button";
import { Dialog, DialogClose, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Sheet, SheetClose, SheetContent, SheetFooter, SheetHeader, SheetTitle, SheetTrigger } from "@/components/ui/sheet";
import webConfig from "@/constants/common-env";
import { fetchSiteSettings, fetchThirdPartyApps, type SiteSettings, type ThirdPartyAppsSettings } from "@/lib/api";
import { getValidatedAuthSession } from "@/lib/auth-session";
import { cn } from "@/lib/utils";
import { clearStoredAuthSession, type StoredAuthSession } from "@/store/auth";

const adminNavItems = [
  { href: "/image", label: "生图" },
  { href: "/accounts", label: "号池管理" },
  { href: "/register", label: "注册机" },
  { href: "/image-manager", label: "图片管理" },
  { href: "/logs", label: "日志管理" },
  { href: "/debug", label: "调试" },
  { href: "/settings", label: "设置" },
];

const userNavItems = [{ href: "/image", label: "画图" }];

const DEFAULT_SITE: SiteSettings = {
  name: "chatgpt2api",
  logo_url: "",
  github_label: "GitHub",
  github_url: "https://github.com/basketikun/chatgpt2api",
  announcement: {
    enabled: false,
    title: "公告",
    content: "",
  },
};

function buildThirdPartyHref(appUrl: string, baseUrl: string, apiKey: string) {
  const url = appUrl.trim();
  try {
    const target = new URL(url);
    target.searchParams.set("apiKey", apiKey);
    target.searchParams.set("baseUrl", baseUrl);
    return target.toString();
  } catch {
    return `${url}${url.includes("?") ? "&" : "?"}apiKey=${encodeURIComponent(apiKey)}&baseUrl=${encodeURIComponent(baseUrl)}`;
  }
}

export function TopNav() {
  const pathname = usePathname();
  const router = useRouter();
  const [session, setSession] = useState<StoredAuthSession | null | undefined>(undefined);
  const [thirdPartyApps, setThirdPartyApps] = useState<ThirdPartyAppsSettings | null>(null);
  const [site, setSite] = useState<SiteSettings>(DEFAULT_SITE);
  const [isCanvasDialogOpen, setIsCanvasDialogOpen] = useState(false);
  const [isAnnouncementDismissed, setIsAnnouncementDismissed] = useState(false);

  useEffect(() => {
    let active = true;

    const load = async () => {
      if (pathname === "/login") {
        if (!active) {
          return;
        }
        setSession(null);
        return;
      }

      const storedSession = await getValidatedAuthSession();
      if (!active) {
        return;
      }
      setSession(storedSession);
    };

    void load();
    return () => {
      active = false;
    };
  }, [pathname]);

  useEffect(() => {
    if (!session) {
      setThirdPartyApps(null);
      return;
    }
    let active = true;
    const load = async () => {
      try {
        const data = await fetchThirdPartyApps();
        if (active) {
          setThirdPartyApps(data.third_party_apps);
        }
      } catch {
        if (active) {
          setThirdPartyApps(null);
        }
      }
    };
    const reload = () => void load();

    void load();
    window.addEventListener("third-party-apps-updated", reload);
    return () => {
      active = false;
      window.removeEventListener("third-party-apps-updated", reload);
    };
  }, [session]);

  useEffect(() => {
    if (!session) {
      setSite(DEFAULT_SITE);
      return;
    }
    let active = true;
    const load = async () => {
      try {
        const data = await fetchSiteSettings();
        if (active) {
          setSite({
            name: String(data.site?.name || DEFAULT_SITE.name),
            logo_url: String(data.site?.logo_url || ""),
            github_label: String(data.site?.github_label || DEFAULT_SITE.github_label),
            github_url: String(data.site?.github_url || DEFAULT_SITE.github_url),
            announcement: {
              enabled: Boolean(data.site?.announcement?.enabled),
              title: String(data.site?.announcement?.title || DEFAULT_SITE.announcement.title),
              content: String(data.site?.announcement?.content || ""),
            },
          });
        }
      } catch {
        if (active) {
          setSite(DEFAULT_SITE);
        }
      }
    };
    const reload = () => void load();

    void load();
    window.addEventListener("site-settings-updated", reload);
    return () => {
      active = false;
      window.removeEventListener("site-settings-updated", reload);
    };
  }, [session]);

  useEffect(() => {
    setIsAnnouncementDismissed(false);
  }, [site.announcement?.enabled, site.announcement?.title, site.announcement?.content]);

  const handleLogout = async () => {
    await clearStoredAuthSession();
    router.replace("/login");
  };

  if (pathname === "/login" || session === undefined || !session) {
    return null;
  }

  const navItems = session.role === "admin" ? adminNavItems : userNavItems;
  const roleLabel = session.role === "admin" ? "管理员" : "普通用户";
  const displayName = session.name.trim() || roleLabel;
  const baseUrl = webConfig.apiUrl.replace(/\/$/, "") || window.location.origin;
  const canvas = thirdPartyApps?.infinite_canvas;
  const canvasHref = canvas?.enabled && canvas.url.trim() ? buildThirdPartyHref(canvas.url, baseUrl, session.key) : "";
  const canvasDisplayHref = canvasHref ? decodeURIComponent(canvasHref) : "";
  const siteName = site.name.trim() || DEFAULT_SITE.name;
  const logoUrl = site.logo_url.trim();
  const announcement = site.announcement;
  const showAnnouncement = Boolean(
    announcement?.enabled
    && String(announcement.content || "").trim()
    && !isAnnouncementDismissed,
  );

  const handleCanvasOpen = () => {
    if (!canvasHref) {
      return;
    }
    setIsCanvasDialogOpen(true);
  };

  const confirmCanvasOpen = () => {
    if (canvasHref) {
      window.open(canvasHref, "_blank", "noopener,noreferrer");
    }
    setIsCanvasDialogOpen(false);
  };

  return (
    <>
      <header className="border-b border-stone-100/50 dark:border-white/10">
        <div className="flex min-h-12 flex-col gap-1 px-3 py-2 sm:h-12 sm:flex-row sm:items-center sm:justify-between sm:gap-3 sm:px-6 sm:py-0">
          <div className="flex items-center justify-between gap-2 sm:justify-start sm:gap-3">
            <Sheet>
              <SheetTrigger className="inline-flex size-8 items-center justify-center text-stone-700 transition hover:text-stone-950 sm:hidden dark:text-stone-200 dark:hover:text-white">
                <Menu className="size-4" />
                <span className="sr-only">打开导航</span>
              </SheetTrigger>
              <SheetContent side="left">
                <SheetHeader>
                  <SheetTitle>{siteName}</SheetTitle>
                  <span className="text-xs text-stone-500 dark:text-stone-400">{roleLabel} · {displayName}</span>
                </SheetHeader>
                <nav className="mt-8 flex flex-col gap-1">
                  {canvasHref ? (
                    <SheetClose asChild>
                      <button
                        type="button"
                        className="flex items-center rounded-xl px-3 py-2.5 text-left text-sm font-medium text-stone-600 transition hover:bg-stone-100 hover:text-stone-950 dark:text-stone-300 dark:hover:bg-white/10 dark:hover:text-white"
                        onClick={handleCanvasOpen}
                      >
                        无限画布
                      </button>
                    </SheetClose>
                  ) : null}
                  {navItems.map((item) => {
                    const active = pathname === item.href;
                    const className = cn(
                      "flex items-center rounded-xl px-3 py-2.5 text-sm font-medium transition",
                      active ? "bg-stone-950 text-white dark:bg-white dark:text-stone-950" : "text-stone-600 hover:bg-stone-100 hover:text-stone-950 dark:text-stone-300 dark:hover:bg-white/10 dark:hover:text-white",
                    );
                    return (
                      <SheetClose asChild key={item.href}>
                        <Link href={item.href} className={className}>{item.label}</Link>
                      </SheetClose>
                    );
                  })}
                </nav>
                <SheetFooter>
                  <button
                    type="button"
                    className="rounded-xl border border-stone-200 px-3 py-2.5 text-left text-sm font-medium text-stone-500 transition hover:text-stone-950 dark:border-white/10 dark:text-stone-300 dark:hover:text-white"
                    onClick={() => void handleLogout()}
                  >
                    退出
                  </button>
                </SheetFooter>
              </SheetContent>
            </Sheet>
            <Link
              href="/image"
              className="flex min-w-0 shrink-0 items-center gap-2 py-1 text-[15px] font-bold tracking-tight text-stone-950 transition hover:text-stone-700 dark:text-stone-50 dark:hover:text-white"
            >
              {logoUrl ? <img src={logoUrl} alt="" className="size-6 shrink-0 rounded object-contain" /> : null}
              <span className="truncate">{siteName}</span>
            </Link>
            <HeaderActions className="ml-auto sm:hidden" showGithubText={false} githubLabel={site.github_label} githubUrl={site.github_url} />
          </div>
          <nav className="hide-scrollbar -mx-1 hidden min-w-0 flex-1 gap-1 overflow-x-auto px-1 sm:mx-0 sm:flex sm:justify-center sm:gap-8 sm:overflow-visible sm:px-0">
            {canvasHref ? (
              <button
                type="button"
                onClick={handleCanvasOpen}
                className="relative shrink-0 whitespace-nowrap rounded-full px-2.5 py-1 text-[13px] font-medium text-stone-500 transition hover:text-stone-900 sm:rounded-none sm:px-0 sm:text-[15px] dark:text-stone-400 dark:hover:text-stone-100"
              >
                无限画布
              </button>
            ) : null}
            {navItems.map((item) => {
              const active = pathname === item.href;
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  className={cn(
                    "relative shrink-0 whitespace-nowrap rounded-full px-2.5 py-1 text-[13px] font-medium transition sm:rounded-none sm:px-0 sm:text-[15px]",
                    active
                      ? "bg-stone-950 text-white sm:bg-transparent sm:font-semibold sm:text-stone-950 dark:bg-white dark:text-stone-950 dark:sm:bg-transparent dark:sm:text-white"
                      : "text-stone-500 hover:text-stone-900 dark:text-stone-400 dark:hover:text-stone-100",
                  )}
                >
                  {item.label}
                  {active ? <span className="absolute inset-x-0 -bottom-[1px] hidden h-0.5 bg-stone-950 dark:bg-white sm:block" /> : null}
                </Link>
              );
            })}
          </nav>
          <div className="hidden items-center justify-end gap-2 sm:flex sm:gap-3">
            <HeaderActions githubLabel={site.github_label} githubUrl={site.github_url} />
            <span className="hidden rounded-md bg-stone-100 px-2 py-1 text-[10px] font-medium text-stone-500 dark:bg-white/8 dark:text-stone-300 sm:inline-block sm:text-[11px]">
              {roleLabel} · {displayName}
            </span>
            <button
              type="button"
              className="py-1 text-xs text-stone-400 transition hover:text-stone-700 dark:text-stone-500 dark:hover:text-stone-200 sm:text-sm"
              onClick={() => void handleLogout()}
            >
              退出
            </button>
          </div>
        </div>
      </header>
      {showAnnouncement ? (
        <div className="fixed top-16 right-4 z-50 w-[min(calc(100vw-2rem),360px)] rounded-xl border border-amber-200 bg-white/95 p-4 text-stone-800 shadow-xl shadow-stone-900/10 backdrop-blur dark:border-amber-500/30 dark:bg-stone-950/95 dark:text-stone-100">
          <div className="flex items-start gap-3">
            <div className="mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-lg bg-amber-100 text-amber-700 dark:bg-amber-500/15 dark:text-amber-200">
              <Megaphone className="size-4" />
            </div>
            <div className="min-w-0 flex-1">
              <div className="pr-8 text-sm font-semibold text-stone-950 dark:text-white">
                {String(announcement.title || DEFAULT_SITE.announcement.title)}
              </div>
              <div className="mt-1 whitespace-pre-wrap break-words text-sm leading-6 text-stone-600 dark:text-stone-300">
                {String(announcement.content || "")}
              </div>
            </div>
            <button
              type="button"
              className="absolute top-3 right-3 inline-flex size-7 items-center justify-center rounded-lg text-stone-400 transition hover:bg-stone-100 hover:text-stone-700 dark:hover:bg-white/10 dark:hover:text-stone-100"
              onClick={() => setIsAnnouncementDismissed(true)}
              aria-label="关闭公告"
            >
              <X className="size-4" />
            </button>
          </div>
        </div>
      ) : null}
      <Dialog open={isCanvasDialogOpen} onOpenChange={setIsCanvasDialogOpen}>
        <DialogContent showCloseButton={false} className="rounded-2xl p-6">
          <DialogHeader className="gap-2">
            <DialogTitle>跳转到三方应用</DialogTitle>
            <DialogDescription className="text-sm leading-6">
              该入口仅供个人测试使用，建议自行本机部署后再长期使用。跳转地址会默认带上本项目地址和当前密钥，用于自动填充连接信息；如果不放心，可以取消后手动前往应用并自行输入。
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-2">
            <div className="text-xs font-medium text-stone-500">完整跳转地址</div>
            <div className="max-h-28 overflow-auto break-all rounded-xl border border-stone-200 bg-stone-50 px-3 py-2 font-mono text-xs leading-5 text-stone-700">
              {canvasDisplayHref}
            </div>
          </div>
          <DialogFooter className="pt-2">
            <DialogClose asChild>
              <Button type="button" variant="outline" className="rounded-xl border-stone-200 bg-white text-stone-700">
                取消
              </Button>
            </DialogClose>
            <Button type="button" className="rounded-xl bg-stone-950 text-white hover:bg-stone-800" onClick={confirmCanvasOpen}>
              继续跳转
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
