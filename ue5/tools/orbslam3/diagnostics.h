#pragma once
#include <initializer_list>
#include <string>
#include <utility>

namespace gsvdiag {
void start(const std::string& path);
void stop();
void beginFrame(long long frame, double sensorTime);
void emit(const char* event, std::initializer_list<std::pair<const char*, double>> values);
}
