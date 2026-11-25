const pageSeparator = '\n\n'

export type MarkdownDraft = {
  jobId: string | null
  markdown: string
  savedMarkdown: string
  pages?: readonly string[]
  pageAnchors?: boolean
}

function joinPages(pages: readonly string[], anchored = false): string {
  return (anchored ? pages.map((page, index) => `<!-- source-page: ${index + 1} -->\n\n${page}`) : pages).join(pageSeparator)
}

export function createMarkdownDraft(
  jobId: string | null = null,
  markdown = '',
  pages?: readonly string[],
  pageAnchors = false,
): MarkdownDraft {
  // A page edit is safe only when the excerpts reconstruct the complete source.
  const exactPages = pages?.length && pages.every(page => page.trim()) && joinPages(pages, pageAnchors) === markdown
  return {jobId, markdown, savedMarkdown: markdown, pages: exactPages ? pages : undefined, pageAnchors}
}

export function updateMarkdownDraft(draft: MarkdownDraft, text: string, pageIndex?: number): MarkdownDraft {
  if (pageIndex === undefined || !draft.pages) {
    return {...draft, markdown: text, pages: undefined}
  }

  const pages = draft.pages.map((page, index) => index === pageIndex ? text : page)
  return {...draft, markdown: joinPages(pages, draft.pageAnchors), pages}
}

export function hidePageAnchors() {
  return (tree: {children: {type: string; value?: string}[]}) => {
    tree.children = tree.children.filter(node => node.type !== 'html' || !/^<!-- source-page: [1-9]\d* -->$/.test(node.value ?? ''))
  }
}
