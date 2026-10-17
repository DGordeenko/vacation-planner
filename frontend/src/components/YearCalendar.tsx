import { buildMonth, inRange, iso, isWeekend, MONTHS, WEEKDAYS } from "../dates";
import type { Holiday, ScheduleEntry, TagAvailability } from "../types";

interface Props {
  year: number;
  entries: ScheduleEntry[];
  holidays: Map<string, Holiday>;
  activeTag: TagAvailability | null;
  pending: { from: string; to: string } | null;
  locked: boolean;
  onDayClick: (key: string) => void;
  onDayHover: (key: string) => void;
}

export default function YearCalendar({
  year, entries, holidays, activeTag, pending, locked, onDayClick, onDayHover,
}: Props) {
  const entryAt = (key: string) =>
    entries.find((e) => inRange(key, e.date_from, e.date_to || e.date_from));

  const busyAt = (key: string) => {
    if (!activeTag) return [];
    return activeTag.colleagues_on_leave.filter(
      (c) => inRange(key, c.date_from, c.date_to)
    );
  };

  return (
    <div className="vs-grid">
      {MONTHS.map((label, monthIndex) => (
        <section className="vs-month" key={label}>
          <h3>{label}</h3>
          <div className="vs-week">
            {WEEKDAYS.map((day) => <div className="vs-wd" key={day}>{day}</div>)}
            {buildMonth(year, monthIndex).map((cell, index) => {
              if (!cell) return <div className="vs-day void" key={`v${index}`} />;

              const key = iso(cell);
              const entry = entryAt(key);
              const holiday = holidays.get(key);
              const busy = busyAt(key);
              const limit = activeTag?.limit ?? null;
              const conflict = limit !== null && busy.length >= limit;
              const marking = pending && inRange(key, pending.from, pending.to);

              const classes = ["vs-day"];
              if (isWeekend(cell)) classes.push("weekend");
              if (holiday?.day_type === "holiday") classes.push("holiday");
              if (conflict && !entry) classes.push("conflict");
              if (entry) {
                classes.push(entry.status === "draft" ? "draft" : "booked");
              }
              if (marking) classes.push("marking");

              const hints = [key, holiday?.name];
              if (busy.length) hints.push(`В отпуске: ${busy.map((b) => b.name).join(", ")}`);

              return (
                <button key={key} className={classes.join(" ")} disabled={locked}
                  onClick={() => onDayClick(key)}
                  onMouseEnter={() => onDayHover(key)}
                  title={hints.filter(Boolean).join(" · ")}>
                  {cell.getUTCDate()}
                </button>
              );
            })}
          </div>
        </section>
      ))}
    </div>
  );
}
