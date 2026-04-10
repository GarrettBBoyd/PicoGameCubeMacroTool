#!/usr/bin/env python3
"""Convert a .bin file to UF2 format for RP2350."""
import struct, sys

def convert(bin_path, uf2_path, base_addr=0x10000000, family_id=0xe48bff59):
    with open(bin_path, 'rb') as f:
        data = f.read()

    BLOCK_SIZE = 256
    num_blocks = (len(data) + BLOCK_SIZE - 1) // BLOCK_SIZE

    with open(uf2_path, 'wb') as f:
        for i in range(num_blocks):
            chunk = data[i*BLOCK_SIZE:(i+1)*BLOCK_SIZE]
            padding = b'\x00' * (476 - len(chunk))
            block = struct.pack('<IIIIIIII',
                0x0A324655,  # magic start 0
                0x9E5D5157,  # magic start 1
                0x00002000,  # flags (family ID present)
                base_addr + i * BLOCK_SIZE,
                BLOCK_SIZE,
                i,
                num_blocks,
                family_id
            ) + chunk + padding + struct.pack('<I', 0x0AB16F30)
            f.write(block)

    print(f"Wrote {num_blocks} blocks ({len(data)} bytes) to {uf2_path}")

if __name__ == '__main__':
    convert(sys.argv[1], sys.argv[2])
