import SwiftUI

struct ChatGPTTokenTotals: Decodable {
    let input_tokens: Int
    let output_tokens: Int
    let total_tokens: Int
    let requests: Int
    let incomplete_requests: Int
}

struct ChatGPTUsageDay: Decodable, Identifiable {
    let date: String
    let total_tokens: Int
    var id: String { date }
    var shortDate: String { String(date.suffix(5)).replacingOccurrences(of: "-", with: "/") }
}

struct ChatGPTUsageSummary: Decodable {
    let profile_id: String
    let today: ChatGPTTokenTotals
    let last_30_days: ChatGPTTokenTotals
    let daily: [ChatGPTUsageDay]
    let tracking_started_at: Double?
    let updated_at: Double?
}

struct ChatGPTWeeklyUsage: Decodable {
    let available: Bool
    let account_email: String?
    let remaining_percent: Double?
    let used_percent: Double?
    let resets_at: Double?
    let updated_at: Double?
    let message: String?
}

struct ChatGPTUsageView: View {
    @EnvironmentObject private var connection: ChatGPTConnection
    @EnvironmentObject private var settings: TranslatorModel

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack {
                Text("이 Mac의 KPaper 사용량")
                    .font(.system(size: 12, weight: .semibold))
                Spacer()
                Button {
                    connection.refreshUsage(repoPath: settings.repoPath)
                } label: {
                    Image(systemName: "arrow.clockwise")
                }
                .buttonStyle(.plain)
                .help("토큰 사용량 새로고침")
                .accessibilityLabel("토큰 사용량 새로고침")
                .disabled(connection.usageBusy || connection.busy)
            }

            if let usage = connection.usage {
                HStack(alignment: .firstTextBaseline, spacing: 28) {
                    metric("오늘", tokens: usage.today.total_tokens, requests: usage.today.requests)
                    metric("최근 30일", tokens: usage.last_30_days.total_tokens, requests: usage.last_30_days.requests)
                }

                VStack(alignment: .leading, spacing: 6) {
                    Text("오늘의 토큰 구성")
                        .font(.system(size: 11))
                        .foregroundStyle(.secondary)
                    tokenBar(usage.today)
                    HStack(spacing: 16) {
                        tokenLegend("입력", count: usage.today.input_tokens, color: .accentColor)
                        tokenLegend("출력", count: usage.today.output_tokens, color: .teal)
                    }
                }

                if usage.last_30_days.requests > 0 {
                    VStack(alignment: .leading, spacing: 6) {
                        Text("최근 7일 · 일별 토큰")
                            .font(.system(size: 11))
                            .foregroundStyle(.secondary)
                        HStack(alignment: .bottom, spacing: 10) {
                            ForEach(usage.daily) { day in
                                VStack(spacing: 4) {
                                    RoundedRectangle(cornerRadius: 3)
                                        .fill(day.id == usage.daily.last?.id ? Color.accentColor : Color.accentColor.opacity(0.35))
                                        .frame(height: max(2, 34 * Double(day.total_tokens) / Double(max(1, usage.daily.map(\.total_tokens).max() ?? 1))))
                                    Text(day.shortDate).font(.system(size: 9)).foregroundStyle(.secondary)
                                }
                                .frame(maxWidth: .infinity, alignment: .bottom)
                                .accessibilityElement(children: .ignore)
                                .accessibilityLabel("\(day.date), \(day.total_tokens.formatted()) 토큰")
                                .help("\(day.date) · \(day.total_tokens.formatted()) 토큰")
                            }
                        }
                        .frame(height: 52, alignment: .bottom)
                    }
                }

                if let start = usage.tracking_started_at {
                    Text("\(Date(timeIntervalSince1970: start).formatted(date: .abbreviated, time: .omitted))부터 기록 · 10초마다 갱신")
                        .font(.system(size: 10)).foregroundStyle(.secondary)
                } else {
                    Text("다음 번역이나 질문부터 토큰 사용량을 기록합니다.")
                        .font(.system(size: 11)).foregroundStyle(.secondary)
                }
                if usage.last_30_days.incomplete_requests > 0 {
                    Text("중단·실패한 요청도 서버가 보고한 토큰은 집계에 포함됩니다.")
                        .font(.system(size: 10)).foregroundStyle(.secondary)
                }
            } else if connection.usageMessage.isEmpty {
                ProgressView().controlSize(.small)
            }

            if !connection.usageMessage.isEmpty {
                Text(connection.usageMessage).font(.system(size: 11)).foregroundStyle(.orange)
            }

            Divider()
            ChatGPTWeeklyUsageView()

            Text("토큰 통계는 사용량이 보고된 KPaper 요청만 집계합니다. 주간 게이지는 Codex의 한도이며, KPaper의 ChatGPT 앱 한도는 ‘사용량 관리’에서 확인하세요.")
                .font(.system(size: 11)).foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            Link("ChatGPT 사용량 관리 ↗", destination: URL(string: "https://chatgpt.com/settings/usage")!)
                .font(.system(size: 12))
        }
    }

    private func metric(_ title: String, tokens: Int, requests: Int) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(title).font(.system(size: 11)).foregroundStyle(.secondary)
            HStack(alignment: .firstTextBaseline, spacing: 4) {
                Text(tokens.formatted()).font(.system(size: 23, weight: .semibold)).monospacedDigit()
                Text("토큰").font(.system(size: 11)).foregroundStyle(.secondary)
            }
            Text("\(requests.formatted())회 요청").font(.system(size: 10)).foregroundStyle(.secondary)
        }
    }

    private func tokenLegend(_ title: String, count: Int, color: Color) -> some View {
        HStack(spacing: 5) {
            Circle().fill(color).frame(width: 6, height: 6)
            Text("\(title) \(count.formatted())").font(.system(size: 11)).monospacedDigit()
        }
    }

    private func tokenBar(_ totals: ChatGPTTokenTotals) -> some View {
        GeometryReader { geometry in
            HStack(spacing: 0) {
                if totals.total_tokens > 0 {
                    Rectangle().fill(Color.accentColor)
                        .frame(width: geometry.size.width * Double(totals.input_tokens) / Double(totals.total_tokens))
                    Rectangle().fill(Color.teal)
                } else {
                    Rectangle().fill(Color.secondary.opacity(0.15))
                }
            }
            .clipShape(Capsule())
        }
        .frame(height: 8)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("오늘의 토큰 구성: 입력 \(totals.input_tokens.formatted()), 출력 \(totals.output_tokens.formatted())")
    }
}

struct ChatGPTUsageSidebar: View {
    @EnvironmentObject private var connection: ChatGPTConnection
    let openStatistics: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Image(systemName: "chart.xyaxis.line")
                Text("GPT 사용량").fontWeight(.semibold)
            }
            .font(.system(size: 11))
            .foregroundStyle(.secondary)

            if let usage = connection.usage {
                VStack(alignment: .leading, spacing: 3) {
                    HStack(alignment: .firstTextBaseline, spacing: 4) {
                        Text(usage.today.total_tokens.formatted())
                            .font(.system(size: 21, weight: .semibold)).monospacedDigit()
                            .lineLimit(1).minimumScaleFactor(0.65)
                        Text("토큰").font(.system(size: 10)).foregroundStyle(.secondary)
                    }
                    Text("오늘 · \(usage.today.requests.formatted())회 요청")
                        .font(.system(size: 10)).foregroundStyle(.secondary)
                }

                VStack(spacing: 5) {
                    ChatGPTUsageSparkline(values: usage.daily.map { Double($0.total_tokens) })
                        .stroke(Color.accentColor, style: StrokeStyle(lineWidth: 2, lineCap: .round, lineJoin: .round))
                        .frame(height: 36)
                        .overlay(alignment: .bottom) {
                            Rectangle().fill(Color.secondary.opacity(0.15)).frame(height: 1)
                        }
                        .accessibilityElement(children: .ignore)
                        .accessibilityLabel("최근 7일 토큰 사용량: " + usage.daily.map { "\($0.shortDate) \($0.total_tokens.formatted())" }.joined(separator: ", "))
                    HStack {
                        Text(usage.daily.first?.shortDate ?? "")
                        Spacer()
                        Text("7일")
                        Spacer()
                        Text(usage.daily.last?.shortDate ?? "")
                    }
                    .font(.system(size: 9)).foregroundStyle(.secondary)
                }
                if !connection.usageMessage.isEmpty {
                    Text("갱신 실패 · 마지막 기록")
                        .font(.system(size: 9)).foregroundStyle(.orange)
                }
            } else if connection.busy {
                ProgressView().controlSize(.small)
            } else {
                Text(connection.usageMessage.isEmpty ? "ChatGPT 연결 후 사용량을 기록합니다." : connection.usageMessage)
                    .font(.system(size: 10)).foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }

            if connection.planEnabled {
                Divider()
                ChatGPTWeeklyUsageView(compact: true)
            }

            Button(action: openStatistics) {
                HStack {
                    Text(connection.active == nil ? "연결하기" : "상세 통계")
                    Spacer()
                    Image(systemName: "arrow.up.right").font(.system(size: 9))
                }
                .font(.system(size: 10))
                .foregroundStyle(Color.accentColor)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .accessibilityLabel("GPT 사용량 상세 통계 열기")
        }
        .padding(14)
        .background(Color(nsColor: .textBackgroundColor).opacity(0.7))
        .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 12, style: .continuous).strokeBorder(Color.secondary.opacity(0.10)))
        .help("이 Mac에서 KPaper의 ChatGPT 로그인으로 사용한 토큰입니다. 계정 전체 한도는 사용량 관리에서 확인하세요.")
    }
}

private struct ChatGPTWeeklyUsageView: View {
    @EnvironmentObject private var connection: ChatGPTConnection
    @EnvironmentObject private var settings: TranslatorModel
    var compact = false

    var body: some View {
        VStack(alignment: .leading, spacing: compact ? 8 : 6) {
            HStack {
                Text("Weekly usage").font(.system(size: compact ? 11 : 12, weight: .semibold))
                Spacer(minLength: 2)
                Button {
                    connection.refreshWeeklyUsage(repoPath: settings.repoPath, force: true)
                } label: {
                    Image(systemName: "arrow.clockwise").font(.system(size: 10))
                        .frame(width: 22, height: 22)
                        .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .accessibilityLabel("Codex 주간 한도 새로고침")
                .help("Codex 계정의 주간 한도 새로고침")
                .disabled(connection.weeklyBusy || connection.busy)
            }

            if let weekly = connection.weeklyUsage, weekly.available,
               let remaining = weekly.remaining_percent, let resets = weekly.resets_at, resets > Date().timeIntervalSince1970 {
                Text("\(remaining.formatted(.number.precision(.fractionLength(0...1))))% 남음")
                    .font(.system(size: compact ? 12 : 17, weight: .semibold)).monospacedDigit()
                ProgressView(value: remaining, total: 100)
                    .tint(remaining <= 10 ? Color.red : remaining <= 25 ? Color.orange : Color.accentColor)
                    .accessibilityLabel("Codex 주간 한도 잔량")
                    .accessibilityValue("\(remaining.formatted())% 남음")
                if compact {
                    VStack(alignment: .leading, spacing: 3) {
                        Text("Codex 주간 한도")
                        Text("\(Date(timeIntervalSince1970: resets).formatted(.dateTime.month(.twoDigits).day(.twoDigits).hour(.twoDigits(amPM: .omitted)).minute())) 초기화")
                    }
                    .font(.system(size: 10)).foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
                } else {
                    Text("Codex · \(Date(timeIntervalSince1970: resets).formatted(.dateTime.month(.twoDigits).day(.twoDigits).hour().minute())) 초기화")
                        .font(.system(size: 11)).foregroundStyle(.secondary)
                }
                if !connection.weeklyMessage.isEmpty {
                    Text("\(connection.weeklyMessage) · 마지막 조회 값")
                        .font(.system(size: 9)).foregroundStyle(.orange)
                }
            } else {
                Text(connection.weeklyBusy ? "주간 한도 조회 중…" : connection.weeklyUsage?.message ?? "주간 한도 조회 대기")
                    .font(.system(size: compact ? 9 : 11)).foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .frame(minHeight: compact ? 96 : nil, alignment: .topLeading)
        .help("연결된 이메일과 일치하는 Codex 로그인 계정의 주간 한도입니다. KPaper의 Sign in with ChatGPT 사용 한도는 ChatGPT 사용량 관리에서 확인하세요.")
    }
}

private struct ChatGPTUsageSparkline: Shape {
    let values: [Double]

    func path(in rect: CGRect) -> Path {
        var path = Path()
        guard !values.isEmpty else { return path }
        let maximum = max(1, values.max() ?? 1)
        let plot = rect.insetBy(dx: 1, dy: 3)
        for (index, value) in values.enumerated() {
            let point = CGPoint(x: plot.minX + plot.width * Double(index) / Double(max(1, values.count - 1)),
                                y: plot.maxY - plot.height * value / maximum)
            if index == 0 { path.move(to: point) }
            else { path.addLine(to: point) }
        }
        return path
    }
}
