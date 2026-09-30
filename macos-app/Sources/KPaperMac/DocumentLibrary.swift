import Foundation

enum OutputKind {
    case korean
    case bilingual
}

struct OutputDocument: Identifiable {
    let url: URL
    let paperID: String
    let modifiedAt: Date
    let byteCount: Int64
    let isBilingual: Bool
    let title: String
    let koreanURL: URL?
    let bilingualURL: URL?
    let originalPDFURL: URL?

    var id: String { paperID }
    var fileName: String { url.lastPathComponent }
    var formatLabel: String { [koreanURL != nil ? "한국어" : nil, bilingualURL != nil ? "한영 비교" : nil, originalPDFURL != nil ? "PDF" : nil].compactMap { $0 }.joined(separator: " · ") }
    var byteCountLabel: String { ByteCountFormatter.string(fromByteCount: byteCount, countStyle: .file) }
}


struct RecentDocument: Codable, Identifiable {
    let paperID: String
    let openedAt: Date
    var id: String { paperID }
}

struct DocumentLibrary {
    static func load(from folder: URL) throws -> [OutputDocument] {
        var isDirectory: ObjCBool = false
        guard FileManager.default.fileExists(atPath: folder.path, isDirectory: &isDirectory) else { return [] }
        guard isDirectory.boolValue else {
            throw NSError(domain: "DocumentLibrary", code: 1, userInfo: [NSLocalizedDescriptionKey: "outputs 경로가 폴더가 아닙니다: \(folder.path)"])
        }
        let keys: Set<URLResourceKey> = [.isRegularFileKey, .contentModificationDateKey, .fileSizeKey]
        var groups: [String: [URL]] = [:]
        for url in try FileManager.default.contentsOfDirectory(at: folder, includingPropertiesForKeys: Array(keys), options: [.skipsHiddenFiles]) {
            let name = url.lastPathComponent
            let suffix = name.hasSuffix(".ko-en.paper.html") ? ".ko-en.paper.html" : name.hasSuffix(".ko.paper.html") ? ".ko.paper.html" : ""
            guard !suffix.isEmpty, (try? url.resourceValues(forKeys: keys).isRegularFile) == true else { continue }
            groups[String(name.dropLast(suffix.count)), default: []].append(url)
        }
        return groups.map { paperID, files in
            let korean = files.first { $0.lastPathComponent.hasSuffix(".ko.paper.html") }
            let bilingual = files.first { $0.lastPathComponent.hasSuffix(".ko-en.paper.html") }
            let preferred = korean ?? bilingual!
            let pdf = folder.deletingLastPathComponent().appendingPathComponent("inputs/pdfs/\(paperID).pdf")
            let values = files.compactMap { try? $0.resourceValues(forKeys: keys) }
            return OutputDocument(url: preferred, paperID: paperID,
                modifiedAt: values.compactMap(\.contentModificationDate).max() ?? .distantPast,
                byteCount: values.reduce(Int64(0)) { $0 + Int64($1.fileSize ?? 0) },
                isBilingual: korean == nil, title: title(at: preferred, paperID: paperID),
                koreanURL: korean, bilingualURL: bilingual,
                originalPDFURL: FileManager.default.fileExists(atPath: pdf.path) ? pdf : nil)
        }.sorted {
            if $0.modifiedAt == $1.modifiedAt { return $0.title.localizedStandardCompare($1.title) == .orderedAscending }
            return $0.modifiedAt > $1.modifiedAt
        }
    }

    static func title(at url: URL, paperID: String) -> String {
        guard let html = try? String(contentsOf: url, encoding: .utf8) else { return readableID(paperID) }
        let prefix = String(html.prefix(1_000_000))
        let h1 = heading("h1", in: prefix)
        // PDF imports can carry a filename in h1; the first page heading is the paper title.
        if let h1, !isPageLabel(h1), !h1.contains("_"), canonical(h1) != canonical(paperID) { return h1 }
        if let firstPage = prefix.range(of: #"<section\b[^>]*\bid=["']page-1["'][^>]*>"#, options: .regularExpression),
           let title = heading("h2", in: String(prefix[firstPage.upperBound...])), !isPageLabel(title) { return title }
        if let h1, !isPageLabel(h1), canonical(h1) != canonical(paperID) {
            return h1.replacingOccurrences(of: "_", with: " ")
        }
        return readableID(paperID)
    }

    private static func isPageLabel(_ value: String) -> Bool {
        value.range(of: #"^(?:pdf\s*)?(?:page|페이지)\s*\d+\s*[.:]?$"#, options: [.regularExpression, .caseInsensitive]) != nil
    }

    private static func heading(_ tag: String, in html: String) -> String? {
        guard let expression = try? NSRegularExpression(pattern: "<\(tag)\\b[^>]*>(.*?)</\(tag)>", options: [.caseInsensitive, .dotMatchesLineSeparators]),
              let match = expression.firstMatch(in: html, range: NSRange(html.startIndex..., in: html)),
              let range = Range(match.range(at: 1), in: html) else { return nil }
        var title = String(html[range]).replacingOccurrences(of: "<[^>]+>", with: " ", options: .regularExpression)
        for (entity, text) in [("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"), ("&quot;", "\""), ("&#39;", "'"), ("&nbsp;", " ")] {
            title = title.replacingOccurrences(of: entity, with: text)
        }
        title = title.replacingOccurrences(of: "\\s+", with: " ", options: .regularExpression).trimmingCharacters(in: .whitespacesAndNewlines)
        return title.isEmpty ? nil : title
    }
    private static func canonical(_ value: String) -> String { value.lowercased().filter { $0.isLetter || $0.isNumber } }
    private static func readableID(_ value: String) -> String { value.replacingOccurrences(of: "-", with: " ").replacingOccurrences(of: "_", with: " ") }
}
