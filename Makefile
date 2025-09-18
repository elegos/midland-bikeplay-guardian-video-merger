all: prerequisites mbg-merger.bin

prerequisites:
	pipenv install --dev

mbg-merger: convert.py
	pipenv run nuitka --standalone --onefile --output-filename=mbg-merger $<

clean:
	rm -f mbg-merger
	rm -rf convert.build
	rm -rf convert.dist

.PHONY: all install-deps clean
