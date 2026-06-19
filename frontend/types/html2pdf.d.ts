// html2pdf.js n'embarque pas ses types — déclaration minimale pour le build.
declare module 'html2pdf.js' {
  interface Html2PdfWorker {
    set(opt: Record<string, unknown>): Html2PdfWorker
    from(element: HTMLElement | string): Html2PdfWorker
    save(): Promise<void>
    toPdf(): Html2PdfWorker
    outputPdf(type?: string): Promise<unknown>
  }
  function html2pdf(): Html2PdfWorker
  export default html2pdf
}
