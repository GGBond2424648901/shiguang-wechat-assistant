export type FabricRole = { id: string; name: string; version: string; author: string; languages?: string[] };

const languageNames = new Map([
  ["zh", "中文"], ["ja", "日语"], ["en", "英语"], ["ko", "韩语"], ["yue", "粤语"], ["auto", "自动识别"],
]);
const languageLabels = new Set(languageNames.values());

export function fabricLanguageLabel(language: string) {
  return languageNames.get(language.toLowerCase().replace(/^all_/, "")) ?? language;
}

export function fabricRoleLabel(role: FabricRole) {
  const name = role.name || role.id;
  const suffix = name.match(/[（(]([^（）()]+)[）)]$/)?.[1];
  if (suffix?.split(/[/／、，,]/).every(label => languageLabels.has(label.trim()))) return name;
  const languages = [...new Set((role.languages ?? []).map(fabricLanguageLabel).filter(Boolean))];
  const chinese = languages.indexOf("中文");
  if (chinese > 0) languages.unshift(...languages.splice(chinese, 1));
  return languages.length ? `${name}（${languages.join("/")}）` : name;
}
