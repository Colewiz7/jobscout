const rules = new Intl.PluralRules("en");

export function countLabel(count, one, many = `${one}s`) {
  return `${new Intl.NumberFormat().format(count)} ${rules.select(count) === "one" ? one : many}`;
}
