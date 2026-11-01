import { useTranslation } from "react-i18next";
import { OrganizeWizard } from "./features/organize/OrganizeWizard";

export function App() {
  const { t } = useTranslation();
  return (
    <main className="mx-auto max-w-3xl space-y-6 p-8">
      <h1 className="text-2xl font-semibold">{t("app.title")}</h1>
      <OrganizeWizard />
    </main>
  );
}
