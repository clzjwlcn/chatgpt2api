"use client";

import { ThemeToggle } from "@/components/theme-toggle";
import { VersionReleaseDialog } from "@/components/version-release-dialog";
import { cn } from "@/lib/utils";

const DEFAULT_GITHUB_URL = "https://github.com/basketikun/chatgpt2api";
const DEFAULT_GITHUB_LABEL = "GitHub";

export function HeaderActions({
  className,
  showGithubText = true,
  githubLabel = DEFAULT_GITHUB_LABEL,
  githubUrl = DEFAULT_GITHUB_URL,
}: {
  className?: string;
  showGithubText?: boolean;
  githubLabel?: string;
  githubUrl?: string;
}) {
  const normalizedGithubUrl = githubUrl.trim() || DEFAULT_GITHUB_URL;
  const normalizedGithubLabel = githubLabel.trim() || DEFAULT_GITHUB_LABEL;
  return (
    <div className={cn("flex items-center gap-2 sm:gap-3", className)}>
      <ThemeToggle />
      <a
        href={normalizedGithubUrl}
        target="_blank"
        rel="noreferrer"
        className="inline-flex h-8 items-center justify-center gap-1.5 text-sm text-stone-500 transition hover:text-stone-900 dark:text-stone-300 dark:hover:text-white"
        aria-label={normalizedGithubLabel}
      >
        <img src="/github.svg" alt="" className="size-4" />
        {showGithubText ? <span className="hidden sm:inline">{normalizedGithubLabel}</span> : null}
      </a>
      <VersionReleaseDialog />
    </div>
  );
}
