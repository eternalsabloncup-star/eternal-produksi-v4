import {
  buildGuidedSetupSteps,
  guideStatusLabel,
  type GuideStepStatus,
} from "../guidedSetup";
import type { JobConfigStatus, SettingsTabId } from "../settingsNav";
import type { ResourceLoadState } from "../loadState";

type Props = {
  rolesCount: number;
  usersCount: number;
  usersWithBranch: number;
  machinesCount: number;
  shiftsCount: number;
  jobStatuses: JobConfigStatus[];
  load: {
    roles?: ResourceLoadState;
    users?: ResourceLoadState;
    branches?: ResourceLoadState;
    machines?: ResourceLoadState;
    shifts?: ResourceLoadState;
    masterJobs?: ResourceLoadState;
  };
  onNavigate: (id: SettingsTabId) => void;
};

function pillClass(status: GuideStepStatus): string {
  if (status === "SIAP") return "is-ok";
  if (status === "BELUM_TERVERIFIKASI") return "is-warn";
  return "is-warn";
}

export function GuidedSetupPanel(props: Props) {
  const steps = buildGuidedSetupSteps(props);

  return (
    <section className="ep-settings-card">
      <h3 className="ep-settings-card-title">Panduan penyiapan</h3>
      <p className="ep-settings-card-note">
        Langkah berdasarkan data tersimpan. Data kosong bisa sengaja belum diatur — bukan
        otomatis dianggap cacat produksi. Tidak ada simpan otomatis.
      </p>
      <ol className="ep-settings-guide-list">
        {steps.map((step) => (
          <li key={step.id} className="ep-settings-guide-item">
            <div className="ep-settings-guide-main">
              <strong>{step.title}</strong>
              <span className={`ep-settings-status-pill ${pillClass(step.status)}`}>
                {guideStatusLabel(step.status)}
              </span>
              <p className="ep-settings-card-note">{step.detail}</p>
            </div>
            <button
              type="button"
              className="ep-btn"
              onClick={() => props.onNavigate(step.targetTab)}
            >
              {step.actionLabel}
            </button>
          </li>
        ))}
      </ol>
    </section>
  );
}
