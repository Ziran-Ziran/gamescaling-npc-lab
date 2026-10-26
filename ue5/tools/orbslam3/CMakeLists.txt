cmake_minimum_required(VERSION 3.16)
project(gsv_rgbd LANGUAGES CXX)
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(ORB_ROOT "" CACHE PATH "Existing ORB-SLAM3 source/build root")
if(NOT EXISTS "${ORB_ROOT}/lib/libORB_SLAM3.so")
  message(FATAL_ERROR "Set ORB_ROOT to an existing installation")
endif()
find_package(OpenCV 4 REQUIRED)
find_package(Eigen3 3.4 REQUIRED NO_MODULE)
find_package(Pangolin REQUIRED)
find_package(Threads REQUIRED)
add_executable(gsv_rgbd runner.cpp)
target_compile_options(gsv_rgbd PRIVATE -Wall -Wextra -O3)
target_include_directories(gsv_rgbd PRIVATE
  ${ORB_ROOT} ${ORB_ROOT}/include ${ORB_ROOT}/include/CameraModels
  ${ORB_ROOT}/Thirdparty/Sophus ${OpenCV_INCLUDE_DIRS} ${Pangolin_INCLUDE_DIRS})
target_link_libraries(gsv_rgbd PRIVATE ${ORB_ROOT}/lib/libORB_SLAM3.so
  ${OpenCV_LIBS} ${Pangolin_LIBRARIES} Eigen3::Eigen Threads::Threads)
set_target_properties(gsv_rgbd PROPERTIES BUILD_RPATH "${ORB_ROOT}/lib")

option(GSV_BUILD_DIAGNOSTICS "Build a separate instrumented library from copied original objects" OFF)
set(GSV_DIAGNOSTIC_SOURCES "" CACHE PATH "Output of orbslam3_diagnostics.py prepare")
if(GSV_BUILD_DIAGNOSTICS)
  if(NOT EXISTS "${GSV_DIAGNOSTIC_SOURCES}/provenance.json")
    message(FATAL_ERROR "Prepare a new diagnostic source/object directory first")
  endif()
  find_package(Boost REQUIRED COMPONENTS serialization)
  find_package(OpenSSL REQUIRED)
  file(GLOB_RECURSE original_objects "${GSV_DIAGNOSTIC_SOURCES}/objects/*.o")
  set_source_files_properties(${original_objects} PROPERTIES EXTERNAL_OBJECT TRUE)
  add_library(gsv_orb_diagnostics SHARED ${original_objects}
    "${GSV_DIAGNOSTIC_SOURCES}/Tracking.cc"
    "${GSV_DIAGNOSTIC_SOURCES}/LocalMapping.cc" diagnostics.cpp)
  target_include_directories(gsv_orb_diagnostics PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR} ${ORB_ROOT} ${ORB_ROOT}/include
    ${ORB_ROOT}/include/CameraModels ${ORB_ROOT}/Thirdparty/Sophus
    ${OpenCV_INCLUDE_DIRS} ${Pangolin_INCLUDE_DIRS})
  target_compile_definitions(gsv_orb_diagnostics PRIVATE COMPILEDWITHC11)
  target_compile_options(gsv_orb_diagnostics PRIVATE -O3 -DNDEBUG -march=native)
  target_link_options(gsv_orb_diagnostics PRIVATE -Wl,--no-undefined)
  target_link_libraries(gsv_orb_diagnostics PRIVATE ${OpenCV_LIBS} ${Pangolin_LIBRARIES}
    ${ORB_ROOT}/Thirdparty/DBoW2/lib/libDBoW2.so
    ${ORB_ROOT}/Thirdparty/g2o/lib/libg2o.so
    Boost::serialization OpenSSL::Crypto Eigen3::Eigen Threads::Threads)
  add_executable(gsv_rgbd_diagnostics runner.cpp)
  target_compile_definitions(gsv_rgbd_diagnostics PRIVATE GSV_DIAGNOSTICS)
  target_compile_options(gsv_rgbd_diagnostics PRIVATE -O3)
  target_include_directories(gsv_rgbd_diagnostics PRIVATE
    ${ORB_ROOT} ${ORB_ROOT}/include ${ORB_ROOT}/include/CameraModels
    ${ORB_ROOT}/Thirdparty/Sophus ${OpenCV_INCLUDE_DIRS} ${Pangolin_INCLUDE_DIRS})
  target_link_libraries(gsv_rgbd_diagnostics PRIVATE gsv_orb_diagnostics
    ${OpenCV_LIBS} ${Pangolin_LIBRARIES} Eigen3::Eigen Threads::Threads)
endif()
