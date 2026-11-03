CXX ?= g++
CXXFLAGS ?= -std=c++17 -Wall -Wextra -Werror -O1 -DSSCS_HOST_TEST -Wno-deprecated-declarations
PYTHON ?= python3

BUILD := build
SRC := ../components/sscs_garage/sscs_protocol.cpp

.PHONY: test clean
test: $(BUILD)/test_protocol
	./$(BUILD)/test_protocol

$(BUILD)/vectors.inc: reference.py | $(BUILD)
	$(PYTHON) reference.py > $@

$(BUILD)/test_protocol: test_protocol.cpp $(SRC) $(BUILD)/vectors.inc
	$(CXX) $(CXXFLAGS) -I$(BUILD) -o $@ test_protocol.cpp $(SRC) -lcrypto

$(BUILD):
	mkdir -p $(BUILD)

clean:
	rm -rf $(BUILD)
