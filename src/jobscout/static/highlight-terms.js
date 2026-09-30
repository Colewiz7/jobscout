"use strict";

// Posting cues, not eligibility verdicts. Keep phrases exact and review broad
// additions against real descriptions before making them automatic.
export const HIGHLIGHT_LABELS = {
  blocker: "Check requirement",
  caution: "Caution",
  fit: "Co-op fit",
  money: "Money",
  stack: "Stack match",
  logistics: "Application step",
  boilerplate: "Boilerplate",
};

const TERMS = {
  blocker: [
    "security clearance", "active clearance", "clearance required", "current clearance",
    "Secret clearance", "Top Secret", "TS/SCI", "SCI clearance", "polygraph", "CI poly",
    "full scope poly", "Public Trust", "Q clearance", "L clearance", "SCIF",
    "ability to obtain clearance", "must be able to obtain clearance", "eligible for clearance",
    "clearance eligibility", "Department of Defense", "DoD", "Intelligence Community",
    "U.S. citizen", "US citizen", "U.S. citizenship", "citizens only", "ITAR", "EAR",
    "export control", "export-controlled", "U.S. person", "US person", "green card",
    "permanent resident", "no sponsorship", "will not sponsor", "sponsorship not available",
    "not eligible for visa sponsorship", "without the need for employer sponsorship",
    "dual citizenship", "foreign nationals", "work authorization required",
    "Master's required", "Master's degree", "MS required", "PhD", "doctoral",
    "graduate student", "graduate program", "recent graduate", "new grad", "new graduate",
    "graduating by", "graduation date", "bachelor's degree required", "completed degree",
    "degree conferred", "must have graduated", "full-time only", "permanent position",
    "not an internship", "experienced hire", "W2 contract", "part-time only", "temporary",
    "outside the US", "must reside in", "must be located in", "local candidates only",
    "onsite only", "fully on-site", "fully onsite", "in-office 5 days", "5 days in office",
    "EMEA", "APAC", "LATAM", "senior", "staff", "principal", "architect",
    "director", "expert-level", "subject matter expert", "extensive experience",
    "proven track record", "years of professional experience", "years of industry experience",
  ],
  caution: [
    "help desk", "helpdesk", "service desk", "Tier 1", "Tier 2", "Level 1",
    "desktop support", "deskside support", "IT technician", "field technician",
    "NOC technician", "ticketing", "imaging", "reimaging", "end user support",
    "walk-up support", "hardware repair", "printer", "printers", "desktops",
    "laptops", "mobile devices", "service requests", "first point of contact",
    "customer-facing support", "password resets", "cabling",
    "asset management", "inventory", "fast-paced", "fast paced environment",
    "wear many hats", "rockstar", "ninja", "guru", "wizard", "superstar",
    "work hard play hard", "like a family", "self-starter", "hustle", "grind",
    "other duties as assigned", "thrive under pressure", "tight deadlines", "high-pressure",
    "startup mentality", "scrappy", "go-getter", "above and beyond", "unlimited PTO",
    "unpaid", "volunteer", "commission", "commission-based", "equity only",
    "equity-based", "1099", "independent contractor", "contract-to-hire",
    "academic credit", "for credit", "stipend only", "no compensation",
    "depends on experience", "competitive pay", "on-call", "on call rotation",
    "24/7", "rotating shifts", "shift work", "nights", "overnight", "weekends",
    "holidays", "travel required", "travel up to", "extended hours", "overtime required",
    "maintenance windows", "after hours", "Federal Government contract", "government contract",
    "federal contractor", "labor categories", "contract wage rates", "LCAT",
    "contingent upon contract award", "CACI", "Leidos", "Booz Allen", "SAIC",
    "GDIT", "Northrop Grumman", "Lockheed Martin", "Raytheon", "RTX",
    "BAE Systems", "L3Harris", "ManTech", "Peraton", "background check",
    "drug screen", "drug test", "credit check", "driver's license required",
    "valid driver's license", "physical requirements", "lift 50 pounds", "fingerprinting",
  ],
  fit: [
    "co-op", "coop", "co-operative", "cooperative education", "intern", "internship",
    "student", "undergraduate", "undergrad", "student worker", "apprentice",
    "apprenticeship", "early career", "university program", "campus program",
    "emerging talent", "trainee", "rising junior", "rising senior", "currently enrolled",
    "Spring 2027", "Summer 2027", "Fall 2027", "January 2027", "May 2027",
    "4-month", "6-month", "8-month", "12-week", "16-week", "rolling start",
    "flexible start date", "multiple start dates", "rolling basis", "year-round",
    "part-time during semester", "return offer", "full-time offer",
    "conversion to full-time", "pathway to full-time", "mentorship", "mentor",
    "buddy program", "intern cohort", "intern program", "intern events",
    "intern project", "capstone project", "production", "ownership", "demo day",
    "speaker series", "networking events", "hackathon", "remote", "fully remote",
    "remote-first", "Remote US", "hybrid", "flexible location", "relocation assistance",
    "relocation provided", "housing provided", "corporate housing", "commuter benefits",
    "learning and development", "training", "tuition assistance", "tuition reimbursement",
    "continuing education", "certification reimbursement", "conference budget",
    "learning stipend", "lunch and learns", "upskilling", "career development",
    "professional development", "hands-on", "cross-functional", "rotational",
  ],
  money: [
    "salary range", "pay range", "proposed salary range", "base pay", "hourly rate",
    "hourly", "per hour", "/hr", "/hour", "an hour", "compensation",
    "total compensation", "stipend", "housing stipend", "living stipend",
    "relocation bonus", "relocation stipend", "sign-on bonus", "signing bonus",
    "overtime", "time and a half", "shift differential", "USD", "401(k)",
    "401k match", "paid holidays", "PTO", "paid time off", "sick leave",
    "health insurance", "medical", "dental", "vision", "meal stipend",
    "free lunch", "gym", "wellness stipend", "equipment stipend",
    "home office stipend", "laptop provided", "transit", "parking", "ESPP", "RSU",
  ],
  stack: [
    "Kubernetes", "k8s", "k3s", "Argo CD", "ArgoCD", "GitOps", "Helm",
    "Kustomize", "Proxmox", "Cloudflare", "Cloudflare Tunnel", "Cloudflare Zero Trust",
    "Authentik", "SSO", "OIDC", "OAuth", "OAuth2", "SAML", "PostgreSQL",
    "Postgres", "CloudNativePG", "CNPG", "Docker", "Docker Compose",
    "Golang", "Linux", "Arch Linux", "Debian", "Bash", "shell scripting",
    "Python", "Git", "GitHub", "CronJob", "ntfy", "homelab", "self-hosted",
    "Traefik", "reverse proxy", "CI/CD", "continuous integration",
    "continuous delivery", "continuous deployment", "pipelines", "build pipelines",
    "GitHub Actions", "GitLab CI", "Jenkins", "CircleCI", "Buildkite",
    "Travis CI", "TeamCity", "Bamboo", "Azure DevOps", "Azure Pipelines",
    "Tekton", "Spinnaker", "Argo Workflows", "Argo Rollouts", "Flux",
    "FluxCD", "Artifactory", "Nexus", "Harbor", "SonarQube", "blue-green",
    "canary", "Terraform", "OpenTofu", "Pulumi", "Ansible", "Chef", "Puppet",
    "SaltStack", "Packer", "CloudFormation", "CDK", "Bicep", "ARM templates",
    "Crossplane", "infrastructure as code", "IaC", "configuration management",
    "Vagrant", "cloud-init", "AWS", "Azure", "GCP", "Google Cloud", "EC2",
    "S3", "EKS", "ECS", "Fargate", "Lambda", "RDS", "VPC", "IAM",
    "Route 53", "CloudWatch", "AKS", "GKE", "Cloud Run", "OCI",
    "DigitalOcean", "Linode", "serverless", "multi-cloud", "hybrid cloud",
    "OpenStack", "containers", "containerization", "Podman", "containerd",
    "CRI-O", "container registry", "OpenShift", "Rancher", "Nomad", "Istio",
    "Linkerd", "service mesh", "virtualization", "VMware", "vSphere", "ESXi",
    "KVM", "Hyper-V", "QEMU", "LXC", "Prometheus", "Grafana", "Loki",
    "Tempo", "Mimir", "Thanos", "ELK", "Elasticsearch", "Logstash",
    "Kibana", "OpenSearch", "Fluentd", "Fluent Bit", "OpenTelemetry",
    "Jaeger", "Datadog", "New Relic", "Splunk", "Dynatrace", "PagerDuty",
    "Opsgenie", "observability", "monitoring", "alerting", "logging",
    "tracing", "SLO", "SLI", "SLA", "incident response", "postmortem",
    "SRE", "site reliability", "site reliability engineering", "platform engineering",
    "platform engineer", "infrastructure engineer", "cloud engineer",
    "DevOps engineer", "release engineering", "build engineer", "systems engineer",
    "systems engineering", "systems administrator", "automation engineer",
    "production engineering", "reliability", "developer experience", "DevEx",
    "internal developer platform", "Backstage", "nginx", "HAProxy", "Envoy",
    "load balancer", "ingress", "DNS", "DHCP", "TCP/IP", "BGP", "VLAN",
    "subnetting", "networking", "IP networking", "VPN", "WireGuard", "Tailscale",
    "firewall", "iptables", "nftables", "CDN", "TLS", "SSL", "certificates",
    "cert-manager", "Let's Encrypt", "Rust", "TypeScript", "JavaScript",
    "Node.js", "Java", "C/C++", "C++", "C#", "PowerShell", "YAML", "JSON",
    "Jinja", "Groovy", "Makefile", "SQL", "Lua", "MySQL", "MariaDB",
    "Redis", "MongoDB", "Kafka", "RabbitMQ", "NATS", "etcd", "SQLite",
    "Cassandra", "DynamoDB", "Vault", "HashiCorp Vault", "secrets management",
    "External Secrets", "Sealed Secrets", "SOPS", "AWS Secrets Manager",
    "Key Vault", "KMS", "1Password", "MLOps", "LLMOps", "AI infrastructure",
    "ML infrastructure", "GPU", "GPU cluster", "CUDA", "ROCm", "NVIDIA",
    "model serving", "inference", "vLLM", "Triton", "TensorRT", "Ollama",
    "llama.cpp", "Ray", "Kubeflow", "KServe", "MLflow", "Airflow",
    "Dagster", "Prefect", "data pipelines", "feature store", "vector database",
    "RAG", "LLM", "PyTorch", "Hugging Face", "Slurm", "HPC",
    "Weights & Biases", "DevSecOps", "zero trust", "vulnerability scanning",
    "vulnerability management", "SIEM", "SOAR", "compliance", "SOC 2",
    "NIST", "NIST 800-53", "NIST 800-171", "FedRAMP", "CMMC", "STIG",
    "CIS benchmarks", "hardening", "Trivy", "Snyk", "Falco", "OPA",
    "Gatekeeper", "Kyverno", "SBOM", "supply chain security",
    "least privilege", "RBAC", "pentest", "threat modeling", "CVE",
    "Wazuh", "CrowdStrike", "Claude", "Claude Code", "Codex", "ChatGPT",
    "OpenAI", "Anthropic", "Gemini", "Copilot", "LangChain", "LlamaIndex",
    "TensorFlow", "scikit-learn", "MATLAB", "Simulink", "LabVIEW",
    "SolidWorks", "AutoCAD", "FPGA", "PCB", "ROS", "CAD",
    "telecommunications", "cyber security", "security analysis",
    "protocol analyzers", "Design of Experiments", "DOE", "Siemens NX", "Rhino",
    "3D models", "data analytics", "Office 365", "Outlook", "Windows",
    "Unix", "macOS", "iOS", "cushioning systems",
  ],
  logistics: [
    "deadline", "apply by", "application deadline", "closing date", "cover letter",
    "transcript", "unofficial transcript", "GPA", "minimum GPA", "portfolio",
    "GitHub profile", "GitHub link", "personal website", "references",
    "letters of recommendation", "writing sample", "coding challenge",
    "take-home", "take-home assignment", "HackerRank", "CodeSignal",
    "LeetCode", "technical assessment", "online assessment", "phone screen",
    "technical interview", "behavioral interview", "panel interview",
    "onsite interview", "video interview", "HireVue", "referral",
    "employee referral", "work authorization", "availability",
  ],
  boilerplate: [
    "Equal Opportunity Employer", "EEO", "equal employment opportunity",
    "without regard to", "sexual orientation", "gender identity",
    "national origin", "protected veteran", "veteran status",
    "genetic information", "protected characteristic", "affirmative action",
    "reasonable accommodation", "accommodation request", "E-Verify",
    "drug-free workplace", "pay transparency", "Know Your Rights",
    "fair chance", "arrest and conviction records", "including but not limited to",
    "a host of factors", "competitive compensation", "comprehensive benefits",
    "our employees value", "we are committed to", "diverse and inclusive",
    "at-will", "not an exhaustive list", "subject to change", "may vary",
    "final salary", "privacy notice",
  ],
};

const EXTRA = {
  blocker: [String.raw`\b(?:[2-9]|10)\+\s*years?\b`, String.raw`\bclass of 20\d{2}\b`],
  caution: [String.raw`\b(?:pay|salary|compensation)\s+DOE\b`, String.raw`\bDOE\s+(?:pay|salary|compensation)\b`],
  fit: [String.raw`\b\d{1,2}(?:\s*[-–]\s*\d{1,2})?[ -]?weeks?\b`, String.raw`\b(?:begins?|starts?|starting)\s+in\s+(?:January|February|March|April|May|June|July|August|September|October|November|December)\b`, String.raw`\b(?:Spring|Summer|Fall|Winter)\s+20\d{2}\b`],
  money: [String.raw`\$\s?\d[\d,]*(?:\.\d{1,2})?(?:\s*[-–]\s*\$?\s?\d[\d,]*(?:\.\d{1,2})?)?(?:\s*(?:/hr|per hour|hourly|per year|annually))?`],
  stack: [String.raw`\bGo\s+(?:language|programming|development|services)\b`],
  logistics: [String.raw`\b(?:minimum\s+)?GPA\s*(?:of\s*)?\d(?:\.\d{1,2})?\b`, String.raw`\b(?:January|February|March|April|May|June|July|August|September|October|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)\s+(?:\d{1,2}(?:,?\s+\d{4})?|20\d{2})\b`],
};

const escapeRegExp = (value) => value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
const CATEGORY_ORDER = Object.keys(HIGHLIGHT_LABELS);
const MATCHERS = CATEGORY_ORDER.map((category) => {
  const terms = [...new Set(TERMS[category])].sort((a, b) => b.length - a.length);
  const phrases = terms.map((term) => `(?<![A-Za-z0-9])${escapeRegExp(term)}(?![A-Za-z0-9])`);
  return { category, regex: new RegExp([...EXTRA[category] || [], ...phrases].join("|"), "gi") };
});

function isFalseBlocker(text, value) {
  if (/^senior$/i.test(value) && /\b(?:high school|rising)\s+senior\b/i.test(text)) return true;
  if (!/clearance/i.test(value)) return false;
  return /\b(?:none\s*\/\s*not required|not required|no clearance required|clearance not required)\b/i.test(text);
}

function isFalseCue(text, value, category, start) {
  if (category === "blocker") return isFalseBlocker(text, value);
  if (category === "fit" && /^remote$/i.test(value) && /\bremote\s+(?:access|desktop|support|workstation)\b/i.test(text)) return true;
  if (category === "caution" && /^volunteer$/i.test(value) && /\bvolunteer\s+leave\b/i.test(text)) return true;
  if (category === "money" && /^\$/.test(value)) {
    const after = text.slice(start + value.length, start + value.length + 20);
    if (/^\s*(?:billion|million)\b/i.test(after)) return true;
    if (!/\b(?:pay|salary|hourly|compensation|stipend|wage|bonus|rate|earnings)\b|\/hr|per hour/i.test(text)) return true;
  }
  return false;
}

export function findPostingHighlights(value, { seen = new Set(), limit = 64 } = {}) {
  const text = String(value || "");
  const matches = [];
  for (const { category, regex } of MATCHERS) {
    regex.lastIndex = 0;
    for (const match of text.matchAll(regex)) {
      if (isFalseCue(text, match[0], category, match.index)) continue;
      matches.push({ start: match.index, end: match.index + match[0].length, category, text: match[0] });
    }
  }
  matches.sort((left, right) => left.start - right.start || right.end - left.end || CATEGORY_ORDER.indexOf(left.category) - CATEGORY_ORDER.indexOf(right.category));
  const chosen = [];
  let end = 0;
  let boilerplateMarked = false;
  for (const match of matches) {
    if (match.start < end) continue;
    if (match.category === "boilerplate" && boilerplateMarked) continue;
    const key = `${match.category}:${match.text.toLocaleLowerCase()}`;
    if (seen.has(key)) continue;
    seen.add(key);
    chosen.push(match);
    if (match.category === "boilerplate") boilerplateMarked = true;
    end = match.end;
    if (chosen.length >= limit) break;
  }
  return chosen;
}
