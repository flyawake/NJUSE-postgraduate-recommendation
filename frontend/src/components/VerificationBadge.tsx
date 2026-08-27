import { CheckCheck, CircleSlash, ShieldX } from "lucide-react";
import { useI18n } from "@/lib/i18n";
import { cx } from "@/lib/format";

export type VerificationKind = "verified" | "not-applicable" | "failed" | "not-run";

const META: Record<
  VerificationKind,
  { labelKey: string; titleKey: string; className: string }
> = {
  verified: {
    labelKey: "verification.verified",
    titleKey: "verification.verifiedLong",
    className: "text-success bg-success-muted border-success/40",
  },
  "not-applicable": {
    labelKey: "verification.notApplicable",
    titleKey: "verification.verifiedLong",
    className: "text-muted bg-surface-2 border-border",
  },
  failed: {
    labelKey: "verification.failed",
    titleKey: "verification.failedLong",
    className: "text-danger bg-danger-muted border-danger/40",
  },
  "not-run": {
    labelKey: "verification.notRun",
    titleKey: "verification.notRunLong",
    className: "text-warning bg-warning-muted border-warning/40",
  },
};

/** Verification status — icon + text, semantic colors never used alone. */
export function VerificationBadge({ status }: { status: VerificationKind }) {
  const { t } = useI18n();
  const meta = META[status];
  const Icon = status === "verified" ? CheckCheck : status === "failed" ? ShieldX : CircleSlash;
  return (
    <span
      className={cx(
        "inline-flex items-center gap-1.5 rounded-md border px-2 py-0.5 text-xs font-semibold",
        meta.className
      )}
      title={t(meta.titleKey)}
      role="status"
    >
      <Icon aria-hidden size={12} />
      {t(meta.labelKey)}
    </span>
  );
}

export function verificationKind(
  status?: string | null
): VerificationKind {
  if (status === "VERIFIED") return "verified";
  if (status === "FAILED") return "failed";
  if (status === "NOT_RUN") return "not-run";
  return "not-applicable";
}
