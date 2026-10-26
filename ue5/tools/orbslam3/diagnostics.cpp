#include "diagnostics.h"
#include <chrono>
#include <cmath>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <mutex>
#include <stdexcept>

namespace gsvdiag {
namespace {
struct State {
    std::mutex mutex;
    std::ofstream stream;
    unsigned long long sequence = 0;
};
State& state() {
    // The upstream Shutdown does not join workers. Keep synchronization storage
    // alive until OS process termination, including exception exits.
    static State* value = new State;
    return *value;
}
thread_local long long frameId = -1;
thread_local double timestamp = -1.;
}

void start(const std::string& path) {
    auto& data = state();
    auto& stream = data.stream;
    std::lock_guard<std::mutex> lock(data.mutex);
    if (stream.is_open() || std::filesystem::exists(path))
        throw std::runtime_error("diagnostic log already exists");
    stream.open(path);
    if (!stream) throw std::runtime_error("cannot create diagnostic log");
    stream << std::setprecision(17);
}

void beginFrame(long long frame, double sensorTime) {
    frameId = frame;
    timestamp = sensorTime;
}

void stop() {
    auto& data = state();
    std::lock_guard<std::mutex> lock(data.mutex);
    if (data.stream.is_open()) {
        data.stream.flush();
        data.stream.close();
    }
}

void emit(const char* event, std::initializer_list<std::pair<const char*, double>> values) {
    auto& data = state();
    auto& stream = data.stream;
    std::lock_guard<std::mutex> lock(data.mutex);
    if (!stream.is_open()) return;
    const auto now = std::chrono::steady_clock::now().time_since_epoch();
    stream << "{\"seq\":" << data.sequence++ << ",\"event\":\"" << event
           << "\",\"monotonic_s\":" << std::chrono::duration<double>(now).count()
           << ",\"frame_id\":";
    if (frameId < 0) stream << "null"; else stream << frameId;
    stream << ",\"t_sensor_s\":";
    if (timestamp < 0) stream << "null"; else stream << timestamp;
    for (const auto& item : values) {
        stream << ",\"" << item.first << "\":";
        if (std::isfinite(item.second)) stream << item.second; else stream << "null";
    }
    stream << "}\n";
    stream.flush();
}
}
