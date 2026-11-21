import { useState } from "react";
import type { StoryDemoStep } from "./apiClient";
import { Icon } from "./Icon";
import type { StoryTabLabel } from "./ScenarioOverview";

/**
 * The guided demo of a scenario's story (`story.demo`): a bar pinned to the bottom of the workspace
 * that walks the presenter through the tabs in order — where we are, what to point at, what to say, and
 * a question to put to the assistant. The workspace switches tab on every step; the notes fold away
 * when the screen is shared. Static DOM: nothing animates.
 */
export function StoryDemoBar({
  steps,
  index,
  onIndex,
  onExit,
  onAsk,
  tabLabel,
  besideChat = false,
}: {
  // The chat drawer is open on the right: the bar moves left so both stay usable.
  besideChat?: boolean;
  steps: StoryDemoStep[];
  index: number;
  onIndex: (index: number) => void;
  onExit: () => void;
  onAsk: (question: string) => void;
  tabLabel: StoryTabLabel;
}) {
  const [notes, setNotes] = useState(true);
  const step = steps[index];
  const last = index === steps.length - 1;
  const where = step.tab === "assistant" ? "Assistant" : tabLabel(step.tab);
  return (
    <aside className={`demo-bar${notes ? "" : " demo-bar--collapsed"}${besideChat ? " demo-bar--beside-chat" : ""}`} aria-label="Guided demo">
      <div className="demo-bar-head">
        <span className="demo-bar-step">
          Step {index + 1}/{steps.length}
          {where ? ` · ${where}` : ""}
        </span>
        <span className="demo-bar-title">{step.title}</span>
        <span className="demo-bar-dots">
          {steps.map((s, i) => (
            <button
              key={s.title}
              title={s.title}
              aria-label={`Step ${i + 1}: ${s.title}`}
              className={i === index ? "current" : i < index ? "done" : ""}
              onClick={() => onIndex(i)}
            />
          ))}
        </span>
      </div>
      <div className="demo-bar-controls">
        <button className="btn-secondary" onClick={() => setNotes((n) => !n)} title={notes ? "Hide the presenter notes" : "Show the presenter notes"}>
          {notes ? "Hide notes" : "Notes"}
        </button>
        <button className="btn-secondary" onClick={() => onIndex(index - 1)} disabled={index === 0} aria-label="Previous step">
          <Icon name="back" size={14} />
        </button>
        {last ? (
          <button className="btn-primary" onClick={onExit}>
            Finish
          </button>
        ) : (
          <button className="btn-primary" onClick={() => onIndex(index + 1)}>
            Next <Icon name="arrow" size={14} />
          </button>
        )}
        <button className="btn-secondary" onClick={onExit} aria-label="Leave the guided demo">
          <Icon name="close" size={14} />
        </button>
      </div>
      {notes && (
        <div className="demo-bar-notes">
          <div>
            <h5>Point at</h5>
            <p>{step.show}</p>
          </div>
          <div className="demo-bar-say">
            <h5>Say</h5>
            <p>{step.say}</p>
          </div>
        </div>
      )}
      {notes && step.ask && (
        <button className="btn-secondary demo-bar-ask" onClick={() => onAsk(step.ask!)}>
          <Icon name="chat" size={14} /> Ask: “{step.ask}”
        </button>
      )}
    </aside>
  );
}
