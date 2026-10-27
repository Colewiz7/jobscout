export const COMPANY_FILTERS = [
  ["all", "All companies"],
  ["applied", "With applications"],
  ["contacts", "With contacts"],
  ["manual", "Added by hand"],
  ["hidden", "Hidden"],
];

const filterTests = {
  all: (company) => !company.hidden,
  applied: (company) => !company.hidden && company.applications.length > 0,
  contacts: (company) => !company.hidden && company.contacts_count > 0,
  manual: (company) => !company.hidden && company.manual,
  hidden: (company) => company.hidden,
};

export function companyFilter(value) {
  return Object.hasOwn(filterTests, value) ? value : "all";
}

// merged names still find it
export function filterCompanies(companies, { query = "", show = "all" } = {}) {
  const needle = query.trim().toLocaleLowerCase();
  const test = filterTests[companyFilter(show)];
  return companies.filter((company) => test(company)
    && (!needle || [company.name, ...(company.aliases || [])].some((name) => name.toLocaleLowerCase().includes(needle))));
}

export function mergeTargets(companies, source) {
  return companies.filter((company) => company.key !== source.key && !company.hidden)
    .sort((left, right) => left.name.localeCompare(right.name));
}

function counted(count, one, many) {
  const rule = new Intl.PluralRules("en-US").select(count);
  return `${new Intl.NumberFormat().format(count)} ${rule === "one" ? one : many}`;
}

function listed(parts) {
  return new Intl.ListFormat("en-US", { type: "conjunction" }).format(parts);
}

// names both sides and what moves
export function mergeCopy(source, target) {
  const moving = [
    counted(source.postings.length, "job", "jobs"),
    ...(source.contacts_count ? [counted(source.contacts_count, "contact", "contacts")] : []),
    ...(source.saved_links_count ? [counted(source.saved_links_count, "link", "links")] : []),
  ];
  const notes = source.has_note ? ` ${source.name}'s notes are added below ${target.name}'s.` : "";
  return {
    title: `Merge ${source.name} into ${target.name}?`,
    body: `${listed(moving)} ${moving.length === 1 && source.postings.length === 1 ? "moves" : "move"} to ${target.name}, and later scout results for ${source.name} land there too. ${target.name} keeps its name, ATS account and logo.${notes} You can undo only right after.`,
  };
}

// stored urls predate the check
export function linkHref(url) {
  try {
    const parsed = new URL(String(url || ""));
    return parsed.protocol === "https:" && parsed.hostname ? parsed.href : "";
  } catch { return ""; }
}

export function linkLabel(link) {
  if (link.label) return link.label;
  try { return new URL(link.url).hostname; } catch { return link.url; }
}
