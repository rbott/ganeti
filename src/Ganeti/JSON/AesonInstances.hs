{-# LANGUAGE FlexibleInstances #-}
{-# OPTIONS_GHC -fno-warn-orphans #-}

{-| Aeson orphan instances for Ganeti types that appear inside config
objects.

Kept in a separate, cycle-free module: it depends only on the leaf types
('Ganeti.Types', 'Ganeti.JSON') and aeson, so it can be imported by the
object modules without creating a module cycle. The instances mirror the
existing 'Text.JSON' instances exactly.

-}

{-

Copyright (C) 2026 Ganeti Project Contributors.
All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are
met:

1. Redistributions of source code must retain the above copyright notice,
this list of conditions and the following disclaimer.

2. Redistributions in binary form must reproduce the above copyright
notice, this list of conditions and the following disclaimer in the
documentation and/or other materials provided with the distribution.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS
IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED
TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR
PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR
CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL,
EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO,
PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR
PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF
LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING
NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

-}

module Ganeti.JSON.AesonInstances () where

import Control.Monad (liftM)
import qualified Data.Aeson as A

import Ganeti.JSON (MaybeForJSON(..))
import Ganeti.Types (NonEmpty, Private(..), mkNonEmpty, fromNonEmpty)

instance (A.ToJSON a) => A.ToJSON (NonEmpty a) where
  toJSON = A.toJSON . fromNonEmpty

instance (A.FromJSON a) => A.FromJSON (NonEmpty a) where
  parseJSON v = A.parseJSON v >>= mkNonEmpty

instance (A.ToJSON a) => A.ToJSON (Private a) where
  toJSON (Private x) = A.toJSON x

instance (A.FromJSON a) => A.FromJSON (Private a) where
  parseJSON = liftM Private . A.parseJSON

instance (A.ToJSON a) => A.ToJSON (MaybeForJSON a) where
  toJSON (MaybeForJSON (Just x)) = A.toJSON x
  toJSON (MaybeForJSON Nothing)  = A.Null

instance (A.FromJSON a) => A.FromJSON (MaybeForJSON a) where
  parseJSON A.Null = return $ MaybeForJSON Nothing
  parseJSON x      = liftM (MaybeForJSON . Just) $ A.parseJSON x
