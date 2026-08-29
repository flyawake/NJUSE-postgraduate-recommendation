import { useI18n } from "@/lib/i18n";

/** About / Security page: honest capability and boundary documentation. */
export function AboutSecurityPage() {
  const { t } = useI18n();
  return (
    <div className="mx-auto w-full max-w-4xl space-y-4 px-4 py-8 sm:px-6 lg:px-8 lg:py-10">
      <h1 className="mb-7 text-2xl font-semibold tracking-[-0.035em]">{t("about.title")}</h1>
      <Section title={t("about.range")} body={t("about.rangeText1")} />
      <Section title={t("about.loopback")} body={t("about.loopbackText")} />
      <Section title={t("about.credentials")} body={t("about.credentialsText")} />
      <Section title={t("about.runCommand")} body={t("about.runCommandText")} />
      <Section title={t("about.localData")} body={t("about.localDataText")} />
      <Section title={t("about.deleteSemantics")} body={t("about.deleteSemanticsText")} />
      <Section title={t("about.terminology")} body={t("about.terminologyText")} />
    </div>
  );
}

function Section({ title, body }: { title: string; body: string }) {
  return (
    <section className="rounded-lg border border-border bg-surface p-5 shadow-sm">
      <h2 className="text-sm font-semibold">{title}</h2>
      <p className="mt-2 text-sm leading-7 text-muted">{body}</p>
    </section>
  );
}
