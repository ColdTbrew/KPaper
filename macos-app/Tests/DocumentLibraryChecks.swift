import Foundation

@main
struct DocumentLibraryChecks {
    static func main() throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        let outputs = root.appendingPathComponent("outputs")
        try FileManager.default.createDirectory(at: outputs, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }
        func write(_ filename: String, _ html: String) throws {
            try html.write(to: outputs.appendingPathComponent(filename), atomically: true, encoding: .utf8)
        }
        try write("pdf-import.ko.paper.html", "<h1>pdf_import</h1><section id='page-1'><h2>실제 논문 <em>제목</em></h2></section>")
        try write("pdf-import.ko-en.paper.html", "<h1>pdf_import</h1>")
        try write("another.ko-en.paper.html", "<h1>A real &amp; useful title</h1>")
        try write("ignored.html", "ignored")
        let pdfFolder = root.appendingPathComponent("inputs/pdfs")
        try FileManager.default.createDirectory(at: pdfFolder, withIntermediateDirectories: true)
        try Data().write(to: pdfFolder.appendingPathComponent("pdf-import.pdf"))
        let documents = try DocumentLibrary.load(from: outputs)
        precondition(documents.count == 2, "Variants must group by paper ID")
        let imported = documents.first { $0.paperID == "pdf-import" }!
        precondition(imported.title == "실제 논문 제목", "PDF title must come from first-page heading")
        precondition(imported.koreanURL != nil && imported.bilingualURL != nil && imported.originalPDFURL != nil)
        precondition(imported.url == imported.koreanURL)
        let another = documents.first { $0.paperID == "another" }!
        precondition(another.title == "A real & useful title")
        precondition(another.koreanURL == nil && another.bilingualURL != nil && another.originalPDFURL == nil)
        let history = [RecentDocument(paperID: "pdf-import", openedAt: Date(timeIntervalSince1970: 123))]
        let restored = try JSONDecoder().decode([RecentDocument].self, from: JSONEncoder().encode(history))
        precondition(restored.first?.paperID == history.first?.paperID && restored.first?.openedAt == history.first?.openedAt)
        print("Document library checks passed: grouping, titles, variants, PDF availability, history persistence")
    }
}
