const clean = (value) => String(value).replace(/\s+/g, " ").trim();

// One line per job. When a job has a tagline it goes on the line below, and
// jobs are then separated by a blank line so each listing reads as one block.
export function inboxCopyText(jobs) {
  const blocks = jobs.map((job) => {
    const headline = [job.company, job.title, job.location, job.url]
      .filter(Boolean).map(clean).join(" — ");
    return job.tagline ? `${headline}\n${clean(job.tagline)}` : headline;
  });
  return blocks.join(jobs.some((job) => job.tagline) ? "\n\n" : "\n");
}
