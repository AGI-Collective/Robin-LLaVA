from PIL import Image
from io import BytesIO
import base64

import torch
from transformers import StoppingCriteria
from robin.constants import IMAGE_TOKEN_INDEX


def load_image_from_base64(image):
    return Image.open(BytesIO(base64.b64decode(image)))


def expand2square(pil_img, background_color):
    width, height = pil_img.size
    if width == height:
        return pil_img
    elif width > height:
        result = Image.new(pil_img.mode, (width, width), background_color)
        result.paste(pil_img, (0, (width - height) // 2))
        return result
    else:
        result = Image.new(pil_img.mode, (height, height), background_color)
        result.paste(pil_img, ((height - width) // 2, 0))
        return result


def process_images(images, image_processor, image_aspect_ratio):
    new_images = []
    
    #Hardcoded because reasons.
    image_mean = (0.48145466, 0.4578275, 0.40821073)
    if image_aspect_ratio == 'pad':
        for image in images:

            # TODO: Simon: don't hardcode image mean, also this is duplicated code with train.py
            image_mean = getattr(image_processor, "image_mean", (0.48145466, 0.4578275, 0.40821073))
            image = expand2square(image, tuple(int(x*255) for x in image_mean))

            # TODO: Simon this is nasty, we need a more unified interface here
            if hasattr(image_processor, "preprocess"):
                image = image_processor.preprocess(image, return_tensors='pt')['pixel_values'][0]
            else:
                image = image_processor(image).unsqueeze(0)

            new_images.append(image)
    else:
        return image_processor(images, return_tensors='pt')['pixel_values']

    if all(x.shape == new_images[0].shape for x in new_images):
        new_images = torch.stack(new_images, dim=0)
        
    return new_images

def process_images_easy(images, image_processor, image_aspect_ratio):
    new_images = []
    
    image_mean = (0.48145466, 0.4578275, 0.40821073)
    if image_aspect_ratio == 'pad':
        for image in images:

            image_mean = getattr(image_processor, "image_mean", (0.48145466, 0.4578275, 0.40821073))
            image = expand2square(image, tuple(int(x*255) for x in image_mean))

            if hasattr(image_processor, "preprocess"):
                image = image_processor.preprocess(image, return_tensors='pt')['pixel_values'][0]
            else:
                image = image_processor(image).unsqueeze(0)

            new_images.append(image)
    else:
        return image_processor(images, return_tensors='pt')['pixel_values']

    if all(x.shape == new_images[0].shape for x in new_images):
        new_images = torch.stack(new_images, dim=0)
        
    return new_images

def tokenizer_image_token(prompt, tokenizer, image_token_index=IMAGE_TOKEN_INDEX, return_tensors=None):
    prompt_chunks = [tokenizer(chunk).input_ids for chunk in prompt.split('<image>')]

    def insert_separator(X, sep):
        return [ele for sublist in zip(X, [sep]*len(X)) for ele in sublist][:-1]

    input_ids = []
    offset = 0
    if len(prompt_chunks) > 0 and len(prompt_chunks[0]) > 0 and prompt_chunks[0][0] == tokenizer.bos_token_id:
        offset = 1
        input_ids.append(prompt_chunks[0][0])

    for x in insert_separator(prompt_chunks, [image_token_index] * (offset + 1)):
        input_ids.extend(x[offset:])

    if return_tensors is not None:
        if return_tensors == 'pt':
            return torch.tensor(input_ids, dtype=torch.long)
        raise ValueError(f'Unsupported tensor type: {return_tensors}')
    return input_ids


def get_model_name_from_path(model_path):
    model_path = model_path.strip("/")
    model_paths = model_path.split("/")
    if model_paths[-1].startswith('checkpoint-'):
        return model_paths[-2] + "_" + model_paths[-1]
    else:
        return model_paths[-1]




class KeywordsStoppingCriteria(StoppingCriteria):
    def __init__(self, keywords, tokenizer, input_ids):
        self.keywords = keywords
        self.keyword_ids = []
        self.max_keyword_len = 0
        for keyword in keywords:
            cur_keyword_ids = tokenizer(keyword).input_ids
            if len(cur_keyword_ids) > 1 and cur_keyword_ids[0] == tokenizer.bos_token_id:
                cur_keyword_ids = cur_keyword_ids[1:]
            if len(cur_keyword_ids) > self.max_keyword_len:
                self.max_keyword_len = len(cur_keyword_ids)
            self.keyword_ids.append(torch.tensor(cur_keyword_ids))

        self.keyword_ids = [keyword_id.to(input_ids.device) for keyword_id in self.keyword_ids]

        self.tokenizer = tokenizer
        self.batch_size = input_ids.shape[0]

        # in batch generation, is used to ensure that all samples have reached the stopping criteria
        self.matches = torch.zeros(self.batch_size, dtype=torch.int, device=input_ids.device)

    def __call__(self, output_ids: torch.LongTensor, scores: torch.FloatTensor, **kwargs) -> bool:
        matches = torch.zeros(self.batch_size, dtype=torch.bool, device=output_ids.device)

        for keyword_id in self.keyword_ids:
            matches |= (output_ids[:, -keyword_id.shape[0]:] == keyword_id).all(dim=1)

        for i, local_match, global_match in zip(range(self.batch_size), matches, self.matches):
            if not global_match and local_match:
                self.matches[i] = output_ids.shape[1]
                
        return self.matches.all().item()
