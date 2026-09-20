// html2pdf.js n'embarque pas ses types — déclaration minimale pour le build.
declare module 'html2pdf.js' {
  /** Sous-ensemble de l'objet jsPDF utilisé pour poser le pied de page. */
  interface JsPdfLike {
    internal: {
      getNumberOfPages(): number
      pageSize: { getWidth(): number; getHeight(): number }
    }
    setPage(n: number): JsPdfLike
    setFontSize(size: number): JsPdfLike
    setTextColor(r: number, g: number, b: number): JsPdfLike
    setDrawColor(r: number, g: number, b: number): JsPdfLike
    setLineWidth(w: number): JsPdfLike
    line(x1: number, y1: number, x2: number, y2: number): JsPdfLike
    text(text: string, x: number, y: number, opt?: { align?: 'left' | 'center' | 'right' }): JsPdfLike
  }
  interface Html2PdfWorker {
    set(opt: Record<string, unknown>): Html2PdfWorker
    from(element: HTMLElement | string): Html2PdfWorker
    save(): Promise<void>
    toPdf(): Html2PdfWorker
    outputPdf(type?: string): Promise<unknown>
    /** `get('pdf')` puis `.then(pdf => …)` : retouche le PDF avant `save()`. */
    get(key: 'pdf'): Html2PdfWorker
    then(onFulfilled: (pdf: JsPdfLike) => void): Html2PdfWorker
  }
  function html2pdf(): Html2PdfWorker
  export default html2pdf
}
