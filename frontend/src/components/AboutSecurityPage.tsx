import { useI18n } from "@/lib/i18n";

/** About / Security page: honest capability and boundary documentation. */
export function AboutSecurityPage() {
  const { t } = useI18n();
  return (
    <div className="mx-auto max-w-3xl space-y-5 px-4 py-6 lg:px-8">
      <h1 className="text-lg font-semibold">{t("about.title")}</h1>
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
    <section className="card">
      <h2 className="text-sm font-semibold">{title}</h2>
      <p className="mt-1.5 text-sm leading-relaxed text-muted">{body}</p>
    </section>
  );
}
