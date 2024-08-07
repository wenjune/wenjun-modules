'''
Author: wenjun-VCC
Date: 2024-05-13 22:41:43
LastEditors: wenjun-VCC
LastEditTime: 2024-07-29 15:41:58
FilePath: transformer.py
Description: __discription:__
Email: wenjun.9707@gmail.com
Copyright (c) 2024 by wenjun/VCC, All Rights Reserved. 
'''
import torch
from torch import nn
from torchtyping import TensorType
from typing import Optional, List
import torch.nn.functional as F
from einops import rearrange, repeat, reduce
import math
from beartype import beartype


# implementation from paper "Attention is All You Need"
# https://arxiv.org/abs/1706.03762




class FeedForward(nn.Module):
    
    def __init__(
        self,
        dim: int,
        mlp_hidden_dim: int=2048,
        ac_func=nn.ReLU,
        dropout: float=None,
    ) -> None:
        super(FeedForward, self).__init__()
        
        self.fc1 = nn.Linear(in_features=dim, out_features=mlp_hidden_dim)
        self.ac_func = ac_func()
        self.dropout = nn.Identity() if dropout is None else nn.Dropout(dropout)
        self.fc2 = nn.Linear(in_features=mlp_hidden_dim, out_features=dim)
        
    
    @beartype   
    def forward(
        self,
        x: TensorType['bs','sl', 'dim', float],
    ):
        
        out = self.fc1(x)
        out = self.ac_func(out)
        out = self.dropout(out)
        out = self.fc2(out)
        
        return out



class MultiHeadAttention(nn.Module):
    
    def __init__(
        self,
        d_model: int,
        nheads: int=8,
        qkv_bias: bool=False,
        is_causal: bool=False,
        atten_dropout: float=None,
    ) -> None:
        super(MultiHeadAttention, self).__init__()
        
        assert d_model % nheads == 0, "d_model should divisible by nheads != 0!"
        
        self.d_k = d_model // nheads
        self.d_model = d_model
        self.nheads = nheads
        self.is_causal = is_causal
        self.scale = 1./math.sqrt(self.d_k)
        
        self.dropout = nn.Identity() if atten_dropout is None else nn.Dropout(atten_dropout)
        
        self.Qw = nn.Linear(d_model, d_model, bias=qkv_bias)  # Wq
        self.Kw = nn.Linear(d_model, d_model, bias=qkv_bias)  # Wk
        self.Vw = nn.Linear(d_model, d_model, bias=qkv_bias)  # Wv
        self.Ow = nn.Linear(d_model, d_model, bias=True)
        
    
    @beartype
    def attention(
        self,
        query: TensorType['b', 'ql', 'dim', float],
        key: TensorType['b', 'kl', 'dim', float],
        value: TensorType['b', 'vl', 'dim', float],
        *,  # force to use keyword arguments
        key_padding_mask: Optional[TensorType['bs', 1, 1, 'kl', bool]]=None,
        causal_mask: Optional[TensorType[1, 1, 'ql', 'kl', bool]]=None,
    ):
        ''' 
        Args:
            query: [bs, q_len, dk, float]
            key: [bs, k_len, dk, float]
            value: [bs, v_len, dk, float]
            key_padding_mask: [bs, 1, 1, k_len, bool]
            causal_mask: [1, 1, q_len, k_len, bool]
        Returns:
            output: [bs, q_len, (nh*dk)]
            atten_scores: [bs, nh, q_len, k_len]
        '''
        # reshape (Q, K, V) to multi-head vector
        # query : ->[bs, query_len, heads, dk]
        query = rearrange(query, 'b q (h dk) -> b q h dk', dk=self.d_k)
        # key   : ->[bs, key_len, heads, dk]
        key = rearrange(key, 'b k (h dk) -> b k h dk', dk=self.d_k)
        # value : ->[bs, value_len, heads, dk]
        value = rearrange(value, 'b v (h dk) -> b v h dk', dk=self.d_k)
        
        # calculate attention scores: [batch_size, heads, query_len, key_len]
        attention_scores = torch.einsum('nqhd, nkhd -> nhqk', [query, key]) * self.scale
        
        # apply masks
        # apply key padding mask
        attention_scores = self._apply_mask(
            score=attention_scores,
            mask=key_padding_mask,
        )
        # apply causal mask
        attention_scores = self._apply_mask(
            score=attention_scores,
            mask=causal_mask,
        )

        # calculate attention distribution
        attention_scores = attention_scores.softmax(dim=-1)
        attention_scores = self.dropout(attention_scores)
        # attention_scores = [batch_size, heads, query_len, key_len]
        # value = [batch_size, value_len, heads, d_k]
        # key_len = value_len [batch_size, query_len, heads, d_k]
        output = torch.einsum('nhql, nlhd -> nqhd', [attention_scores, value])
        output = rearrange(output, 'b s h d -> b s (h d)')
        
        return output, attention_scores
    
    
    @beartype
    def _apply_mask(
        self,
        *,  # force to use keyword arguments
        score: TensorType['bs', 'h', 'ql', 'kl', float],
        mask: Optional[TensorType['bs', 1, 1, 'kl', bool]]=None,
    ):
        
        if mask is not None:
            score = score.masked_fill(~mask, float(-1e10))
            return score
        
        else:
            return score
        
        
    @beartype
    def forward(
        self,
        query: TensorType['bs', 'ql', 'dim', float],
        key: TensorType['bs', 'kl', 'dim', float],
        value: TensorType['bs', 'vl', 'dim', float],
        *,  # force to use keyword arguments
        key_padding_mask: Optional[TensorType['bs', 'kl', bool]]=None,  # key padding
        use_cache: bool=False,
        kv_cache=None,  # using kv_cache to accelerate the inference process
    ):
        """ MultiheadAttention forward
            include kv_cache

        Args:
            query (TensorType[bs, ql, dim, float]): in self-atten q=k=v
            key (TensorType[bs, kl, dim, float]): kl=vl
            value (TensorType[bs, vl, dim, float]):
            key_padding_mask (TensorType[bs, kl, bool], optional): mask the padding locations. Defaults to None.
            use_cache(bool, optional): if use kv_cache(just use in inference process)
            kv_cache (dict, optional): cache to accelerate the inference. Defaults to None.

        Returns:
            tuple(out_put, new_cache, atten_score): if not use_cache->new_cache is None
        """
        
        query = self.Qw(query)
        key = self.Kw(key)
        value = self.Vw(value)
        
        if use_cache:
            if kv_cache['key'] is None:  # discreminate key or value is none or not  
                present_key = key
                present_value = value
                
            else:
                present_key = torch.cat([kv_cache['key'], key], dim=1)
                present_value = torch.cat([kv_cache['value'], value], dim=1)
                
            new_cache = {
                'key': present_key,
                'value': present_value,
            }
            
            output, attention_scores = self.attention(
                query, present_key, present_value,
            )
            
            return output, new_cache, attention_scores
        
        causal_mask = None
        if self.is_causal:
            # usually we just use causal mask in decoder self attention
            # ql = kl = vl
            causal_mask = self._make_causal_mask(query)
            causal_mask = rearrange(causal_mask, '... -> 1 1 ...')  # [1, 1, sl, sl]
        if key_padding_mask is not None:   
            key_padding_mask = rearrange(key_padding_mask, 'bs kl -> bs 1 1 kl')
        
        output, attention_scores = self.attention(
            query, key, value,
            key_padding_mask=key_padding_mask,
            causal_mask=causal_mask,
        )
        
        output = self.Ow(output)
        
        return output, None, attention_scores


    @beartype
    def _make_causal_mask(
        self,
        tgt: TensorType['bs', 'sl', 'dim' ,float],
    ):
        """causal mask for autoregressive task

        Args:
            tgt : query

        Returns:
            bool_mask[1, sl, sl] : [[true, false, false],
                                    [ture, true,  false],
                                    [true, true,  true ]]
        """
        
        length = tgt.shape[1]
        mask = torch.tril(torch.ones((length, length), dtype=torch.bool, device=tgt.device))
        
        return mask
    


class EncoderBlock(nn.Module):
    
    def __init__(
        self,
        d_model: int,
        nheads: int=8,
        mlp_hidden_dim: int=2048,
        qkv_bias: bool=False,
        ffd_dropout: float=None,
        atten_dropout: float=None,
        ac_func=nn.ReLU,
        norm=nn.LayerNorm,
    ) -> None:
        super(EncoderBlock, self).__init__()
        
        self.self_atten_block = MultiHeadAttention(
            d_model=d_model,
            nheads=nheads,
            atten_dropout=atten_dropout,
            qkv_bias=qkv_bias,
        )
        
        self.feed_forward_block = FeedForward(
            dim=d_model,
            mlp_hidden_dim=mlp_hidden_dim,
            dropout=ffd_dropout,
            ac_func=ac_func,
        )
        
        self.norm1 = norm(d_model)
        self.norm2 = norm(d_model)
    
    
    @beartype
    def forward(
        self,
        query: TensorType['b', 'sq', 'dim', float],
        *,  # force to use keyword arguments
        src_mask: Optional[TensorType['b', 'sq', bool]]=None,
        return_scores: bool=False,
    ):
        """ transformer encoder block forward

        Args:
            query (TensorType[bs, ql, dim, float]): q=k=v
            src_mask (TensorType[bs, ql, bool], optional): src padding mask. Defaults to None.
            return_scores (bool, optional): if return attention scores. Defaults to False.

        Returns:
            _type_: _description_
        """
        
        # in transformer encoder, we just have the source mask to mask padding tokens
        # and in inference process we didn't use cache
        attention, _, attention_scores = self.self_atten_block(
            query, query, query,
            key_padding_mask=src_mask,
        )
        
        x = self.norm1(query + attention)
        
        forward = self.feed_forward_block(x)
        
        out = self.norm2(forward + x)
        
        if return_scores:
        
            return out, attention_scores
        
        return out, None



class DecoderBlock(nn.Module):
    
    def __init__(
        self,
        d_model: int,
        nheads: int=8,
        mlp_hidden_dim: int=2048,
        qkv_bias: bool=True,
        ffd_dropout: float=None,
        self_atten_dropout: float=None,
        cross_atten_dropout: float=None,
        is_cross_atten: bool=False,
        is_causal: bool=True,
        ac_func=nn.ReLU,
        norm=nn.LayerNorm,
    ) -> None:
        super(DecoderBlock, self).__init__()
        
        self.is_cross_atten = is_cross_atten
        self.is_causal = is_causal
        
        self.self_atten_block = MultiHeadAttention(
            d_model=d_model,
            nheads=nheads,
            qkv_bias=qkv_bias,
            atten_dropout=self_atten_dropout,
            is_causal=is_causal
        )
        
        self.cross_atten_block = MultiHeadAttention(
            d_model=d_model,
            nheads=nheads,
            qkv_bias=qkv_bias,
            atten_dropout=cross_atten_dropout
        ) if is_cross_atten else nn.Identity()
        
        self.feed_forward_block = FeedForward(
            dim=d_model,
            mlp_hidden_dim=mlp_hidden_dim,
            dropout=ffd_dropout,
            ac_func=ac_func,
        )
        
        self.norm1 = norm(d_model)
        self.norm2 = norm(d_model) if is_cross_atten else nn.Identity()
        self.norm3 = norm(d_model)
    
    
    @beartype
    def forward(
        self,
        tgt: TensorType['bs', 'ql', 'dim', float],
        *,  # force to use keyword arguments
        encoder_output: Optional[TensorType['bs', 'kl', 'dim', float]]=None,
        src_mask: Optional[TensorType['bs', 'kl', bool]]=None,
        tgt_mask: Optional[TensorType['bs', 'ql', bool]]=None,
        use_cache: bool=False,
        kv_cache = None,
        return_scores: bool=False,
    ):
        '''
            tgt             : as query for cross atten
            encoder_output  : as key and value for cross atten
            src_mask: from encoder for corss atten block
            tgt_mask: from docoder input for self atten block
        '''
        
        self_attention, new_cache, self_attention_scores = self.self_atten_block(
            tgt, tgt, tgt,
            key_padding_mask=tgt_mask,
            use_cache=use_cache,
            kv_cache=kv_cache,
        )
        
        tgt = self.norm1(tgt + self_attention)
        
        cros_attention_scores = None
        
        if self.is_cross_atten:
            
            cros_attention, _, cros_attention_scores = self.cross_atten_block(
                tgt, encoder_output, encoder_output,
                key_padding_mask=src_mask,
            )
        
            tgt = self.norm2(tgt + cros_attention)
        
        ffd = self.feed_forward_block(tgt)
        
        out = self.norm3(tgt + ffd)
        
        if return_scores:
        
            return out, new_cache, (self_attention_scores, cros_attention_scores)
        
        return out, new_cache, None


class TransformerEncoder(nn.Module):
    
    def __init__(
        self,
        d_model: int,
        depth: int=12,
        nheads: int=8,
        mlp_hidden_dim: int=2048,
        qkv_bias: bool=False,
        ffd_dropout: float=None,
        atten_dropout: float=None,
        ac_func=nn.ReLU,
        norm=nn.LayerNorm,
    ) -> None:
        super(TransformerEncoder, self).__init__()
        
        self.layers = nn.ModuleList([EncoderBlock(
            d_model=d_model,
            nheads=nheads,
            qkv_bias=qkv_bias,
            mlp_hidden_dim=mlp_hidden_dim,
            ffd_dropout=ffd_dropout,
            atten_dropout=atten_dropout,
            ac_func=ac_func,
            norm=norm,
            ) for _ in range(depth)]
        )
    
    
    @beartype
    def forward(
        self,
        src: TensorType['bs', 'sl', 'dim', float],
        *,  # force to use keyword arguments
        src_mask: Optional[TensorType['bs', 'sl', bool]]=None,
        return_scores: bool=False
    ):
        """ transformer encoder forward

        Args:
            src (TensorType[bs, sl, dim, float]): q=k=v
            src_mask (TensorType[bs, sl, bool], optional): src_padding_mask. Defaults to None.
            return_scores (bool, optional): if return attention scores, if False return None. Defaults to False.

        Returns:
            tuple: out, (attention_scores)
        """
        
        for block in self.layers:
            src, attention_scores = block(
                query=src,
                src_mask=src_mask,
                return_scores=return_scores
            )
        
        if return_scores:
            
            return src, attention_scores

        return src

  
class TransformerDecoder(nn.Module):
    
    def __init__(
        self,
        d_model: int,
        depth: int=12,
        nheads: int=8,
        qkv_bias: bool=False,
        mlp_hidden_dim: int=2048,
        ffd_dropout: float=None,
        self_atten_dropout: float=None,
        cross_atten_dropout: float=None,
        is_cross_atten: bool=False,
        is_causal: bool=True,
        ac_func=nn.ReLU,
        norm=nn.LayerNorm,
    ) -> None:
        super(TransformerDecoder, self).__init__()
        
        self.is_cross_atten = is_cross_atten
        self.depth = depth
        
        self.layers = nn.ModuleList([DecoderBlock(
            d_model=d_model,
            nheads=nheads,
            mlp_hidden_dim=mlp_hidden_dim,
            qkv_bias=qkv_bias,
            ffd_dropout=ffd_dropout,
            self_atten_dropout=self_atten_dropout,
            cross_atten_dropout=cross_atten_dropout,
            is_cross_atten=is_cross_atten,
            is_causal=is_causal,
            ac_func=ac_func,
            norm=norm,
            ) for _ in range(depth)]
        )
    
    
    @beartype
    def forward(
        self,
        tgt: TensorType['bs', 'ql', 'dim', float],
        *,  # force to use keyword arguments
        encoder_output: Optional[TensorType['bs', 'kl', 'dim', float]]=None,
        src_mask: Optional[TensorType['bs', 'kl', bool]]=None,
        tgt_mask: Optional[TensorType['bs', 'ql', bool]]=None,
        return_scores: bool=False,
        use_cache: bool=False,
        kv_cache_list = None,
    ):
        """ transformer decoder forward

        Args:
            tgt (TensorType[bs, ql, dim, float]): tgt input (decoder input)
            encoder_output (TensorType[bs, kl, dim, float], optional): for cross attention (k,v). Defaults to None.
            src_mask (TensorType[bs, kl, bool], optional): _description_. Defaults to None.
            tgt_mask (TensorType[bs, ql, bool], optional): _description_. Defaults to None.
            return_scores (bool, optional): if return attention scores. Defaults to False.
            use_cache (bool, optional): cache for inference. Defaults to False.
            kv_cache_list (list[dict], optional): kv_cache_list. Defaults to None.

        Returns:
            tuple: tgt, kv_cache_list, (attention_scores)
        """
            
        for idx, block in enumerate(self.layers):
            cache_temp = None
            
            if use_cache:
                if kv_cache_list is None:
                    # initialize cache list
                    kv_cache_list = self._init_cache()
                # cache for each layer
                cache_temp = kv_cache_list[idx]
            
            tgt, new_cache, attention_scores = block(
                tgt=tgt,
                encoder_output=encoder_output,
                src_mask=src_mask,
                tgt_mask=tgt_mask,
                use_cache=use_cache,
                kv_cache=cache_temp,
            )
            
            if use_cache:
                # update cache
                kv_cache_list[idx] = new_cache
        
        if return_scores:
        
            return tgt, kv_cache_list, attention_scores
        
        return tgt, kv_cache_list
    
    
    def _init_cache(
        self,
    ):
        
        cache_list = []
        for i in range(self.depth):
            cache_list.append(
                {'key': None, 'value': None,}
            )
        
        return cache_list



def top_k_logits(logits: torch.Tensor, k: int) -> torch.Tensor:
    """Masks logits such that logits not in top-k are small

    Args:
        logits: tensor representing network predictions
        k: how many logits to not filter out

    Returns:
        logits: logits with top-k logits remaining intact
    """

    if k == 0:
        return logits
    else:
        values, _ = torch.topk(logits, k)
        k_largest = torch.min(values)
        logits = torch.where(torch.le(logits, k_largest), torch.ones_like(logits) * -1e9, logits)
        return logits


def top_p_logits(logits: torch.Tensor, p: float) -> torch.Tensor:
    """Masks logits using nucleus (top-p) sampling

    Args:
        logits: Network predictions
        top-p: What probability of the predictions we want to keep unmasked
    Returns:
        logits: logits with top-p prob mass remaining intact
    """

    if p == 1:
        return logits
    else:
        logit_shape = logits.shape
        seq, dim = logit_shape[1], logit_shape[2]
        logits = torch.reshape(logits, [-1, dim])
        sorted_logits, sorted_indices = torch.sort(logits, descending=True)
        cumulative_probs = torch.cumsum(F.softmax(sorted_logits, dim=-1), dim=-1)
        cumulative_probs = torch.roll(cumulative_probs, 1, -1)
        cumulative_probs[:, 0] = 0
        sorted_indices_to_remove = (cumulative_probs > p).to(logits.dtype)
        logits_ordered = sorted_logits - sorted_indices_to_remove * 1e9
        logits = logits_ordered.gather(1, sorted_indices.argsort(-1))
        
        return torch.reshape(logits, [-1, seq, dim])

