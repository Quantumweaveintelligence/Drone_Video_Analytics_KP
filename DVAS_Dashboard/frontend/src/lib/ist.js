// Indian Standard Time helpers (evidence plan U1/R2): the UI sends IST calendar dates;
// the backend turns them into [00:00 IST, 24:00 IST) ranges.
const fmt = new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Kolkata", year: "numeric", month: "2-digit", day: "2-digit" });

export const istDate = (d = new Date()) => fmt.format(d);           // "YYYY-MM-DD"

export const istDaysAgo = (n) => istDate(new Date(Date.now() - n * 86400000));

export const istMonday = () => {
  const today = new Date(`${istDate()}T00:00:00+05:30`);
  const back = (today.getUTCDay() + 6) % 7;                          // Monday = 0
  return istDate(new Date(today.getTime() - back * 86400000 + 6 * 3600000));
};

export const fmtIST = (iso) => {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString("en-IN", { timeZone: "Asia/Kolkata", hour12: false }) + " IST";
  } catch {
    return iso;
  }
};
