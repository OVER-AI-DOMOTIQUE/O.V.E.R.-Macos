# -*- coding: utf-8 -*-
"""Mini modèle GGUF (architecture llama) pour les essais : répond toujours la même phrase, très vite."""
import sys
import numpy as np
import gguf

def fabriquer(chemin, phrase="▁Ceci▁est▁un▁essai,▁voix▁Piper."):
    dim, ff, couches, tetes = 32, 64, 1, 2
    jetons = ["<unk>", "<s>", "</s>"] + [f"<0x{i:02X}>" for i in range(256)] + ["▁", phrase]
    types = [2, 3, 3] + [6] * 256 + [1, 1]
    cible = len(jetons) - 1
    n = len(jetons)
    w = gguf.GGUFWriter(chemin, "llama")
    w.add_name("mini-essai")
    w.add_context_length(8192)
    w.add_embedding_length(dim)
    w.add_block_count(couches)
    w.add_feed_forward_length(ff)
    w.add_head_count(tetes)
    w.add_head_count_kv(tetes)
    w.add_rope_dimension_count(dim // tetes)
    w.add_layer_norm_rms_eps(1e-5)
    w.add_file_type(0)
    w.add_tokenizer_model("llama")
    w.add_token_list(jetons)
    w.add_token_scores([0.0] * n)
    w.add_token_types(types)
    w.add_bos_token_id(1)
    w.add_eos_token_id(2)
    w.add_unk_token_id(0)
    w.add_add_bos_token(True)
    w.add_add_eos_token(False)
    w.add_chat_template("{% for m in messages %}{{ m['role'] }}: {{ m['content'] }}\n{% endfor %}assistant:")
    f32 = np.float32
    w.add_tensor("token_embd.weight", np.ones((n, dim), dtype=f32))
    w.add_tensor("output_norm.weight", np.ones(dim, dtype=f32))
    sortie = np.zeros((n, dim), dtype=f32)
    sortie[cible] = 0.2
    w.add_tensor("output.weight", sortie)
    for i in range(couches):
        for nom, forme in (("attn_q", (dim, dim)), ("attn_k", (dim, dim)), ("attn_v", (dim, dim)),
                           ("attn_output", (dim, dim)), ("ffn_gate", (ff, dim)), ("ffn_up", (ff, dim)),
                           ("ffn_down", (dim, ff))):
            w.add_tensor(f"blk.{i}.{nom}.weight", np.zeros(forme, dtype=f32))
        w.add_tensor(f"blk.{i}.attn_norm.weight", np.ones(dim, dtype=f32))
        w.add_tensor(f"blk.{i}.ffn_norm.weight", np.ones(dim, dtype=f32))
    w.write_header_to_file()
    w.write_kv_data_to_file()
    w.write_tensors_to_file()
    w.close()

if __name__ == "__main__":
    fabriquer(sys.argv[1])
